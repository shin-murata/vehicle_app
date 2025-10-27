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

# ✅ 日本時間タイムゾーンを定義
JST = timezone(timedelta(hours=9))


bp = Blueprint('routes', __name__)

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route('/import_csv', methods=['GET', 'POST'])  # ← GETを追加！
def import_csv():
    if request.method == 'GET':
        return render_template("import_csv.html")

    print("\n✅ /import_csv に到達")
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'CSVファイルが必要です'}), 400

    df = pd.read_csv(file, encoding='cp932')

    # ✅ 半角カタカナを全角に統一
    def to_zenkaku(text):
        if isinstance(text, str):
            return unicodedata.normalize('NFKC', text)
        return text

    df = df.applymap(to_zenkaku)
    
    added = 0
    fail_count = 0
    success_count = 0
    fail_ids = []
    processed = 0
    batch_size = 50
    sleep_seconds = 4  # ✅ 安全性向上のため3〜5秒に調整

    for row in df.itertuples():
        print(f"\n🚗 処理中: {row.自社管理番号}")
        # ✅ 型式がNaNや空文字の場合はスキップ
        if pd.isna(row.認定型式) or str(row.認定型式).strip() == "":
            print(f"⚠️ 型式が空またはNaNのためスキップ: {row.自社管理番号}")
            continue

        processed += 1
        if processed > 0 and processed % batch_size == 0:
            print(f"⏸ {batch_size}件処理ごとにコミット＆メモリ解放中...")
            try:
                db.session.commit()        # DB に書き込み確定
                db.session.expunge_all()   # セッションからオブジェクトを外す（メモリ軽減）
                gc.collect()               # Pythonのガベージコレクションを促す
            except Exception as e:
                print("⚠️ バッチコミット中にエラー:", e)
                db.session.rollback()
            time.sleep(sleep_seconds)

        vehicle = Vehicle.query.filter_by(intake_number=row.入庫番号).first()

        if not vehicle:
            vehicle = Vehicle(
                intake_number=row.入庫番号,
                status=row.ステータス,
                condition=row.状態,
                pickup_date=row.引取完了日 if not pd.isna(row.引取完了日) else None,
                client=row.依頼元,
                car_name=row.車名,
                model_code=row.認定型式,
                year=row.年式,
                vin=row.車台番号,
                color=row.車色,
                estimate_price=row.見積金額,
                internal_code=row.自社管理番号
            )
            db.session.add(vehicle)
            print(f"📝 Vehicle 追加: {row.入庫番号}")
        else:
            print(f"📦 Vehicle 既に存在: {row.入庫番号}")
                # ✅ 既存データが None のカラムを CSV の値で補完
            if vehicle.intake_number is None and not pd.isna(row.入庫番号):
                vehicle.intake_number = row.入庫番号

            if vehicle.status is None and not pd.isna(row.ステータス):
                vehicle.status = row.ステータス

            if vehicle.condition is None and not pd.isna(row.状態):
                vehicle.condition = row.状態

            if vehicle.pickup_date is None and not pd.isna(row.引取完了日):
                vehicle.pickup_date = row.引取完了日

            if vehicle.client is None and not pd.isna(row.依頼元):
                vehicle.client = row.依頼元

            if vehicle.car_name is None and not pd.isna(row.車名):
                vehicle.car_name = row.車名

            if vehicle.model_code is None and not pd.isna(row.認定型式):
                vehicle.model_code = row.認定型式

            if vehicle.year is None and not pd.isna(row.年式):
                vehicle.year = row.年式

            if vehicle.vin is None and not pd.isna(row.車台番号):
                vehicle.vin = row.車台番号

            if vehicle.color is None and not pd.isna(row.車色):
                vehicle.color = row.車色

            if vehicle.estimate_price is None and not pd.isna(row.見積金額):
                vehicle.estimate_price = row.見積金額

            # ✅ 変更があった場合に明示的に再追加
            db.session.add(vehicle)

        scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()

        # ✅ 「すでに仮メーカー or 不明」が登録されていればスキップ
        if scraped and scraped.manufacturer_name in ["仮メーカー", "不明"]:
            print(f"⏭ 仮メーカー or 不明は再スクレイピング不要: {row.自社管理番号}")
            continue


        # ✅ 正規化＋接頭辞除去でキーワードを生成
        car_name_normalized = unicodedata.normalize("NFKC", str(row.車名)).replace("・", "")
        model_code_normalized = unicodedata.normalize("NFKC", str(row.認定型式))
        model_code_cleaned = re.sub(r"^[A-Z]+-", "", model_code_normalized)
        keyword = f"{car_name_normalized} {model_code_cleaned}"
        print(f"🔍 キーワード生成: {keyword}")

        # ✅ 過去に同じキーワードで成功していないか履歴チェック
        existing_info = ScrapedInfo.query.filter(
            ScrapedInfo.manufacturer_name.notin_(["不明", "仮メーカー"]),
            ScrapedInfo.vehicle.has(
                and_(
                    Vehicle.car_name == row.車名,
                    Vehicle.model_code == row.認定型式
                )
            )
        ).first()

        if existing_info:
            maker_name = existing_info.manufacturer_name
            print(f"♻️ 既存のメーカー情報を再利用: {maker_name}")
        else:
            time.sleep(sleep_seconds)
            maker_name = scrape_manufacturer(row.車名, row.認定型式)

        if maker_name == "不明":
            fail_count += 1
            fail_ids.append(row.自社管理番号)
            print(f"⚠️ メーカー取得失敗（{fail_count}件目）")
            if scraped:
                scraped.manufacturer_name = "不明"
                scraped.retrieved_date = datetime.now(JST)
                scraped.source_url = "https://www.kurumaerabi.com/"
                print("♻️ 仮メーカーを不明に更新")
            else:
                scraped = ScrapedInfo(
                    vehicle=vehicle,
                    manufacturer_name="不明",
                    model_spec="取得失敗",
                    retrieved_date=datetime.now(JST).date(),
                    source_url="https://www.kurumaerabi.com/"
                )
                db.session.add(scraped)
                print("🆕 不明として scraped_info を新規作成")

            continue
        else:
            success_count += 1
            fail_count = 0

        manufacturer = Manufacturer.query.filter_by(name=maker_name).first()
        if not manufacturer:
            manufacturer = Manufacturer(name=maker_name)
            db.session.add(manufacturer)

        vehicle.manufacturer = manufacturer

        if scraped:
            scraped.manufacturer_name = maker_name
            scraped.model_spec = "取得予定"
            scraped.retrieved_date = datetime.now(JST)
            scraped.source_url = "https://www.kurumaerabi.com/"
            print(f"♻️ スクレイピング情報を更新: {row.自社管理番号}")
        else:
            scraped_info = ScrapedInfo(
                vehicle=vehicle,
                manufacturer_name=maker_name,
                model_spec="取得予定",
                retrieved_date=datetime.now(JST).date(),
                source_url="https://www.kurumaerabi.com/"
            )
            db.session.add(scraped_info)
            print(f"🧾 スクレイピング情報追加: {row.自社管理番号}")

        added += 1

        # ループ終了（ここに残りのコミットを入れる）
    try:
        # バッチでコミットしきれなかった残りを確実に確定
        db.session.commit()
        # セッションからオブジェクトを外してメモリを解放
        db.session.expunge_all()
        # Python の GC を明示的に呼ぶ
        gc.collect()
    except Exception as e:
        print("⚠️ 最終コミットでエラー:", e)
        db.session.rollback()

    # ✅ CSVとして失敗IDを出力
    fail_filename = None

    # ✅ CSVとして失敗IDを出力（static フォルダに保存）
    if fail_ids:
        fail_filename = "failed_ids.csv"
        fail_path = os.path.join("static", fail_filename)  # ← 保存先を変更

        with open(fail_path, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["internal_code"])
            for code in fail_ids:
                writer.writerow([code])
        print(f"📄 失敗データを static/{fail_filename} に書き出しました（{len(fail_ids)}件）")
    # ✅ 成功・失敗に関わらず DB への変更は最後に一括コミット
    db.session.commit()
        
    # ✅ 以前の jsonify を削除して以下に差し替え
    return render_template("import_result.html",
        message=f"{added} 件の車両を登録しました",
        success_count=success_count,
        fail_count=len(fail_ids),
        fail_file="failed_ids.csv" if fail_ids else None
    )


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
