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

@bp.route('/import_csv', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'GET':
        return render_template("import_csv.html")

    print("\n✅ /import_csv に到達")
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'CSVファイルが必要です'}), 400

    # ==== ここから：軽量化 & 再開準備 ===================================
    # ✅ 文字正規化（行処理の中だけで使う：applymapは使わない）
    def to_zenkaku(text):
        if isinstance(text, str):
            return unicodedata.normalize('NFKC', text)
        return text

    # ✅ 読み込む列だけに限定（ロジックは同じ。入庫番号等の列名はCSVどおり）
    USECOLS = [
        "入庫番号", "ステータス", "状態", "引取完了日", "依頼元",
        "車名", "認定型式", "年式", "車台番号", "車色",
        "見積金額", "自社管理番号"
    ]

    # ✅ チャンク/バッチ設定（メモリ削減）
    CHUNK = 50
    batch_size = 20
    sleep_seconds = 4

    # ✅ 再開（resume）機能
    RESUME_LOG_PATH = os.path.join("static", "processed_intake_numbers.txt")
    resume = (request.args.get("resume", "").lower() == "true")

    # 既存の処理済み入庫番号を読み込み
    processed_ids_from_log = set()
    if resume and os.path.exists(RESUME_LOG_PATH):
        with open(RESUME_LOG_PATH, "r") as f:
            for line in f:
                s = line.strip()
                if s:
                    try:
                        processed_ids_from_log.add(int(s))
                    except ValueError:
                        pass
        print(f"♻️ resume: ログから {len(processed_ids_from_log)} 件をスキップ対象として読み込み")
    # ===============================================================

    # pandas のチャンクイテレータ（dtype=str で型膨張抑制）
    file.stream.seek(0)
    chunk_iter = pd.read_csv(
        file.stream,
        encoding='cp932',
        dtype=str,
        chunksize=CHUNK,
        usecols=USECOLS     # ✅ 追加：必要列だけ読む
    )

    added = 0
    fail_ids = []
    success_count = 0
    processed = 0

    # CSV内重複スキップ（チャンク跨ぎ対応）
    seen_in_csv = set()

    # ログ追記用（一括で追記してI/Oを減らす）
    to_log_after_commit: list[int] = []

    def flush_resume_log(ids: list[int]):
        if not ids:
            return
        os.makedirs(os.path.dirname(RESUME_LOG_PATH), exist_ok=True)
        with open(RESUME_LOG_PATH, "a") as f:
            for v in ids:
                f.write(f"{v}\n")
        ids.clear()

    for df in chunk_iter:
        try:
            # ---- 行ループ（ここでだけ正規化して使う）----
            for row in df.itertuples():
                # 取り出し＆軽量正規化（使うものだけ）
                raw_intake = getattr(row, "入庫番号", None)
                raw_code   = getattr(row, "自社管理番号", None)

                # 入庫番号キーの決定
                key = None
                if raw_intake is not None and str(raw_intake).strip() != "" and not pd.isna(raw_intake):
                    try:
                        key = int(str(raw_intake).strip())
                    except ValueError:
                        key = None

                print(f"\n🚗 処理中: {raw_code}")

                if key is None:
                    print("⚠️ 入庫番号が無いためスキップ")
                    continue

                # 再開スキップ（既に処理済み）
                if resume and key in processed_ids_from_log:
                    print(f"⏭ resume: 既処理のためスキップ: {key}")
                    continue

                # 同一CSV内重複スキップ
                if key in seen_in_csv:
                    print(f"⏭ CSV内重複のためスキップ: {key}")
                    continue
                seen_in_csv.add(key)

                # 型式チェック（必要時だけ正規化）
                raw_model = getattr(row, "認定型式", None)
                if (raw_model is None) or pd.isna(raw_model) or str(raw_model).strip() == "":
                    print(f"⚠️ 型式が空またはNaNのためスキップ: {raw_code}")
                    continue

                # バッチ境目でコミット＆メモリ解放
                processed += 1
                if processed > 0 and processed % batch_size == 0:
                    print(f"⏸ {batch_size}件処理ごとにコミット＆メモリ解放中...")
                    try:
                        db.session.commit()
                        db.session.expunge_all()
                        gc.collect()
                        flush_resume_log(to_log_after_commit)  # ✅ ログ追記
                    except Exception as e:
                        print("⚠️ バッチコミット中にエラー:", e)
                        db.session.rollback()
                    time.sleep(sleep_seconds)

                # 既存車両の存在チェック
                vehicle = Vehicle.query.filter_by(intake_number=key).first()

                # 既存＆確定済みは早期スキップ
                if vehicle:
                    scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()
                    if vehicle.manufacturer_id or (scraped and scraped.manufacturer_name not in ["仮メーカー", "不明"]):
                        print(f"⏭ 既存 & メーカー確定済みのためスキップ: {key}")
                        to_log_after_commit.append(key)  # ✅ スキップでも「処理済み」として記録
                        continue

                # ---- Vehicle の新規作成 or 補完 ----
                def nz_str(v):
                    return None if (v is None or pd.isna(v) or str(v).strip() == "") else to_zenkaku(str(v))

                if not vehicle:
                    vehicle = Vehicle(
                        intake_number=key,
                        status=nz_str(getattr(row, "ステータス", None)),
                        condition=nz_str(getattr(row, "状態", None)),
                        pickup_date=nz_str(getattr(row, "引取完了日", None)),
                        client=nz_str(getattr(row, "依頼元", None)),
                        car_name=nz_str(getattr(row, "車名", None)),
                        model_code=nz_str(raw_model),
                        year=nz_str(getattr(row, "年式", None)),
                        vin=nz_str(getattr(row, "車台番号", None)),
                        color=nz_str(getattr(row, "車色", None)),
                        estimate_price=nz_str(getattr(row, "見積金額", None)),
                        internal_code=nz_str(raw_code),
                    )
                    db.session.add(vehicle)
                    print(f"📝 Vehicle 追加: {key}")
                else:
                    print(f"📦 Vehicle 既に存在: {key}")
                    # None の項目だけ補完
                    if vehicle.status is None:
                        vehicle.status = nz_str(getattr(row, "ステータス", None))
                    if vehicle.condition is None:
                        vehicle.condition = nz_str(getattr(row, "状態", None))
                    if vehicle.pickup_date is None:
                        vehicle.pickup_date = nz_str(getattr(row, "引取完了日", None))
                    if vehicle.client is None:
                        vehicle.client = nz_str(getattr(row, "依頼元", None))
                    if vehicle.car_name is None:
                        vehicle.car_name = nz_str(getattr(row, "車名", None))
                    if vehicle.model_code is None:
                        vehicle.model_code = nz_str(raw_model)
                    if vehicle.year is None:
                        vehicle.year = nz_str(getattr(row, "年式", None))
                    if vehicle.vin is None:
                        vehicle.vin = nz_str(getattr(row, "車台番号", None))
                    if vehicle.color is None:
                        vehicle.color = nz_str(getattr(row, "車色", None))
                    if vehicle.estimate_price is None:
                        vehicle.estimate_price = nz_str(getattr(row, "見積金額", None))
                    db.session.add(vehicle)

                scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()
                if scraped and scraped.manufacturer_name in ["仮メーカー", "不明"]:
                    print(f"⏭ 仮メーカー or 不明は再スクレイピング不要: {raw_code}")
                    to_log_after_commit.append(key)  # ✅ 記録だけして次へ
                    continue

                # ---- スクレイピング（キーワード生成も行内で正規化）----
                car_name_norm = to_zenkaku(str(getattr(row, "車名", ""))).replace("・", "")
                model_code_norm = to_zenkaku(str(raw_model))
                model_code_clean = re.sub(r"^[A-Z]+-", "", model_code_norm)
                keyword = f"{car_name_norm} {model_code_clean}"
                print(f"🔍 キーワード生成: {keyword}")

                existing_info = ScrapedInfo.query.filter(
                    ScrapedInfo.manufacturer_name.notin_(["不明", "仮メーカー"]),
                    ScrapedInfo.vehicle.has(
                        and_(Vehicle.car_name == getattr(row, "車名", None),
                             Vehicle.model_code == raw_model)
                    )
                ).first()

                if existing_info:
                    maker_name = existing_info.manufacturer_name
                    print(f"♻️ 既存のメーカー情報を再利用: {maker_name}")
                else:
                    time.sleep(sleep_seconds)
                    maker_name = scrape_manufacturer(getattr(row, "車名", None), raw_model)

                if maker_name == "不明":
                    fail_ids.append(nz_str(raw_code))
                    print(f"⚠️ メーカー取得失敗（累計 {len(fail_ids)}件）")
                    if scraped:
                        scraped.manufacturer_name = "不明"
                        scraped.retrieved_date = datetime.now(JST)
                        scraped.source_url = "https://www.kurumaerabi.com/"
                    else:
                        db.session.add(ScrapedInfo(
                            vehicle=vehicle,
                            manufacturer_name="不明",
                            model_spec="取得失敗",
                            retrieved_date=datetime.now(JST).date(),
                            source_url="https://www.kurumaerabi.com/"
                        ))
                    to_log_after_commit.append(key)  # ✅ 失敗でも処理済みとして記録
                    continue
                else:
                    success_count += 1

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
                    print(f"♻️ スクレイピング情報を更新: {raw_code}")
                else:
                    db.session.add(ScrapedInfo(
                        vehicle=vehicle,
                        manufacturer_name=maker_name,
                        model_spec="取得予定",
                        retrieved_date=datetime.now(JST).date(),
                        source_url="https://www.kurumaerabi.com/"
                    ))
                    print(f"🧾 スクレイピング情報追加: {raw_code}")

                added += 1
                to_log_after_commit.append(key)  # ✅ 正常終了も記録

            # ---- チャンク末尾：忘れずに確定・解放 ----
            db.session.commit()
            db.session.expunge_all()
            gc.collect()
            flush_resume_log(to_log_after_commit)  # ✅ ログ追記
            del df
            gc.collect()

        except Exception as e:
            print("⚠️ チャンク処理中にエラー:", e)
            db.session.rollback()
            # 失敗しても、次のチャンクへ進む（部分成功を活かす）
            try:
                flush_resume_log(to_log_after_commit)
            except Exception:
                pass

    # 失敗IDを書き出し
    if fail_ids:
        fail_filename = "failed_ids.csv"
        fail_path = os.path.join("static", fail_filename)
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["internal_code"])
            for code in fail_ids:
                writer.writerow([code])
        print(f"📄 失敗データを static/{fail_filename} に書き出しました（{len(fail_ids)}件）")

    # 念のための最終コミット
    db.session.commit()

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