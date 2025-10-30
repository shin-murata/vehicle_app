# ---------- 標準ライブラリ ----------
import csv
import os
import re
import time
import unicodedata
import gc  # ← 追加
from datetime import date, datetime, timezone, timedelta

# ---------- サードパーティ ----------
import pandas as pd
from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, flash
)
from sqlalchemy import and_, or_

# ---------- アプリケーション ----------
from app import db
from app.models import (
    Vehicle, Manufacturer, ScrapedInfo,
    Estimation, Buyer, Client
)
from app.forms import EstimationForm
from scraper.scrape_maker import scrape_manufacturer
# ---------- 追加インポート（RQ/Redis 用） ----------
import io
from redis import Redis
from rq import Queue


# ✅ 日本時間タイムゾーンを定義
JST = timezone(timedelta(hours=9))

bp = Blueprint('routes', __name__)
# RQ キュー取得ヘルパー
def _get_queue():
    import os
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    conn = Redis.from_url(redis_url)
    return Queue("default", connection=conn)

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route('/import_csv', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'GET':
        return render_template("import_csv.html")

    # POST: ここではジョブ投入のみ
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'CSVファイルが必要です'}), 400

    # ワーカー側で読むため /tmp に保存（Render は /tmp 書込可）
    tmp_path = f"/tmp/import_{int(time.time())}.csv"
    file.stream.seek(0)
    file.save(tmp_path)

    # resume フラグ（?resume=true 互換）
    resume = (request.args.get("resume", "").lower() == "true")

    # ジョブ投入（モジュールパスの文字列で指定）
    job = q.enqueue(
        "tasks.import_job.process_csv_and_scrape",  # ← ここを文字列に
        tmp_path,
        resume,
        job_timeout=60 * 60 * 6,
        failure_ttl=60 * 60 * 24
    )
    return redirect(url_for('routes.import_status', job_id=job.get_id()))

from rq.job import Job

@bp.route('/jobs/<job_id>/status')
def import_status_api(job_id):
    q = _get_queue()  # 既存の接続をそのまま使う
    try:
        job = Job.fetch(job_id, connection=q.connection)
    except Exception:
        return jsonify({'state': 'unknown'}), 404

    status = job.get_status()  # queued / started / finished / failed
    meta = job.meta or {}

    payload = {
        'state': status,
        'progress': meta.get('progress', 0),
        'processed': meta.get('processed', 0),
        'success': meta.get('success', 0),
        'failed': meta.get('failed', 0),
        'message': meta.get('message', '')
    }

    # 追加：失敗時のスタックと、成功時の戻り値
    if status == 'failed':
        payload['error'] = job.exc_info  # ← これで原因が画面/Networkタブで見える
    if job.is_finished:
        payload['result'] = job.result

    return jsonify(payload)


@bp.route('/jobs/<job_id>')
def import_status(job_id):
    return render_template("import_status.html", job_id=job_id)


# ✅ 一覧表示 & キーワード絞り込みルート
@bp.route("/vehicles", methods=["GET"])
def list_vehicles():
    keyword = request.args.get("keyword", "").strip()

    # ★ デフォルトキーを internal_code に変更
    sort_key    = request.args.get("sort", "internal_code")
    sort_order  = request.args.get("order", "desc")        # 降順固定で OK

    # ✅ 対応可能なソートキー
    sort_fields = {
        "id": Vehicle.id,
        "intake_number": Vehicle.intake_number,
        "internal_code": Vehicle.internal_code,
        "pickup_date": Vehicle.pickup_date,
        "client": Vehicle.client,
    }

    query = Vehicle.query

    # 🔍 キーワード検索
    if keyword:
        query = query.filter(
            or_(
                Vehicle.car_name.ilike(f"%{keyword}%"),
                Vehicle.model_code.ilike(f"%{keyword}%"),
                Vehicle.internal_code.ilike(f"%{keyword}%")
            )
        )

    # ✅ 並び替え指定があれば適用、なければ internal_code の降順で固定
    if sort_key in sort_fields:
        sort_column = sort_fields[sort_key]
        query = query.order_by(sort_column.asc() if sort_order == "asc" else sort_column.desc())
    else:
        # 並び替えが明示されていないとき（初期表示や検索時）
        query = query.order_by(Vehicle.internal_code.desc())
    
    vehicles = query.all()
    return render_template("vehicle_list.html", vehicles=vehicles, keyword=keyword, sort_key=sort_key, sort_order=sort_order)

@bp.route('/new_estimation', methods=['GET', 'POST'])
def new_estimation():
    form = EstimationForm()

    # ✅ マスター選択肢の読み込み
    manufacturers = Manufacturer.query.order_by(Manufacturer.name).all()
    form.maker_select.choices = [('', '選択してください')] + [(m.name, m.name) for m in manufacturers]

    buyers = Buyer.query.order_by(Buyer.name).all()
    form.buyer_select.choices = [('', '選択してください')] + [(b.name, b.name) for b in buyers]

    clients = Client.query.order_by(Client.name).all()
    form.client_select.choices = [('', '選択してください')] + [(c.name, c.name) for c in clients]

    # ✅ GET時：URLパラメータで初期値セット
    if request.method == 'GET':
        form.maker_manual.data = request.args.get('maker', '')
        form.car_name.data = request.args.get('car_name', '')
        form.model_code.data = request.args.get('model_code', '')

    # ✅ POST時：バリデーションと保存処理
    if form.validate_on_submit():
        # ✅ 金額がマイナスでないかチェック（元の処理）
        if form.estimate_price.data is not None and form.estimate_price.data < 0:
            return "❌ 金額は0以上で入力してください", 400

        # ✅ 各項目は「手入力」＞「選択」を優先して採用
        maker = form.maker_manual.data.strip() or form.maker_select.data
        buyer = form.buyer_manual.data.strip() or form.buyer_select.data
        client = form.client_manual.data.strip() or form.client_select.data

        # ✅ 各マスターテーブルに未登録なら追加
        if maker and not Manufacturer.query.filter_by(name=maker).first():
            db.session.add(Manufacturer(name=maker))
        if buyer and not Buyer.query.filter_by(name=buyer).first():
            db.session.add(Buyer(name=buyer))
        if client and not Client.query.filter_by(name=client).first():
            db.session.add(Client(name=client))

        # ✅ Estimation へ登録
        new_entry = Estimation(
            maker=maker,
            car_name=form.car_name.data,
            model_code=form.model_code.data,
            estimate_price=form.estimate_price.data,
            owner=client,  # ←ここにclient名を保存
            sale_price=form.sale_price.data,
            buyer=buyer,
            sold_at=form.sold_at.data,
            note=form.note.data
        )
        db.session.add(new_entry)
        db.session.commit()

        flash("✅ 値付けを登録しました", "success")
        return redirect(url_for('routes.list_vehicles'))

    return render_template("new_estimation.html", form=form)

@bp.route("/estimations")
def list_estimations():
    estimations = Estimation.query.order_by(Estimation.estimated_at.desc()).all()

    # ✅ JST補正を正しく適用
    for est in estimations:
        if est.estimated_at and isinstance(est.estimated_at, datetime):
            # UTCとして扱ってからJSTに変換する
            est.estimated_at = est.estimated_at.replace(tzinfo=timezone.utc).astimezone(JST)

    return render_template("estimation_list.html", estimations=estimations)

@bp.route("/vehicles_missing_manufacturer")
def vehicles_missing_manufacturer():
    vehicles = Vehicle.query.join(ScrapedInfo).filter(ScrapedInfo.manufacturer_name == "不明").all()
    return render_template("vehicles_missing_manufacturer.html", vehicles=vehicles)

@bp.route("/edit_manufacturer/<int:vehicle_id>", methods=["GET", "POST"])
def edit_manufacturer(vehicle_id):
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle_id).first()

    if request.method == "POST":
        # プルダウン or 手入力のどちらかを優先（手入力があればそれを優先）
        selected = request.form.get("manufacturer_name") or ""
        manual = request.form.get("manufacturer_name_custom") or ""
        new_maker = manual.strip() if manual else selected.strip()

        if not new_maker:
            return "❌ メーカー名は必須です", 400

        if scraped:
            scraped.manufacturer_name = new_maker
        else:
            scraped = ScrapedInfo(
                vehicle_id=vehicle.id,
                manufacturer_name=new_maker,
                model_spec="手動入力",
                retrieved_date=datetime.now(JST).date(),
                source_url="manual"
            )
            db.session.add(scraped)

        db.session.commit()
        return redirect(url_for('routes.list_vehicles'))

    # GET時にすべてのマスターメーカーを取得して渡す
    all_manufacturers = Manufacturer.query.order_by(Manufacturer.name).all()

    return render_template(
        "edit_manufacturer.html",
        vehicle=vehicle,
        scraped=scraped,
        all_manufacturers=all_manufacturers
    )

@bp.route('/edit_estimation/<int:id>', methods=['GET', 'POST'])
def edit_estimation(id):
    # 対象の Estimation レコードを取得（なければ 404 エラー）
    estimation = Estimation.query.get_or_404(id)

    # フォームを作成し、初期値として estimation のデータを埋め込む
    form = EstimationForm(obj=estimation)

    # ✅ choices を設定：マスターデータをドロップダウンに反映
    manufacturers = Manufacturer.query.order_by(Manufacturer.name).all()
    form.maker_select.choices = [('', '選択してください')] + [(m.name, m.name) for m in manufacturers]

    buyers = Buyer.query.order_by(Buyer.name).all()
    form.buyer_select.choices = [('', '選択してください')] + [(b.name, b.name) for b in buyers]

    # ✅ フォーム送信後（POST）の処理
    if form.validate_on_submit():
        # maker_manual（手入力欄）が空でないならそちらを優先
        maker_manual = form.maker_manual.data or ""
        if maker_manual.strip():
            selected_maker = maker_manual.strip()
        else:
            selected_maker = form.maker_select.data

        # buyer_manual（手入力欄）が空でないならそちらを優先
        buyer_manual = form.buyer_manual.data or ""
        if buyer_manual.strip():
            selected_buyer = buyer_manual.strip()
        else:
            selected_buyer = form.buyer_select.data

        # Estimation レコードを更新
        estimation.maker = selected_maker
        estimation.buyer = selected_buyer
        estimation.sale_price = form.sale_price.data
        estimation.sold_at = form.sold_at.data
        estimation.note = form.note.data

        # 未登録のメーカーが選ばれたらマスターに追加
        if selected_maker and not Manufacturer.query.filter_by(name=selected_maker).first():
            db.session.add(Manufacturer(name=selected_maker))

        # 未登録の販売先が選ばれたらマスターに追加
        if selected_buyer and not Buyer.query.filter_by(name=selected_buyer).first():
            db.session.add(Buyer(name=selected_buyer))

        db.session.commit()
        flash("✅ 値付け履歴を更新しました", "success")
        return redirect(url_for('routes.list_estimations'))

    return render_template("edit_estimation.html", form=form, estimation=estimation)