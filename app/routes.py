# ✅ 入庫車管理アプリ：CSV取り込み＋スクレイピング構想

# ステップ全体の流れ
# 1. CSVファイルを取り込む
# 2. Vehicleに新規レコードを追加（重複チェックあり）
# 3. 車名 + 型式でスクレイピング
# 4. Manufacturerに追加（重複チェックあり）
# 5. Vehicle.manufacturer_id に外部キーとして紐付け
# 6. ScrapedInfo に履歴として保存（任意）

# --- FlaskルートでのCSVインポート処理例 ---
# ファイル: app/routes.py に追加する

import time
import csv
import re
import unicodedata
import pandas as pd
from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from sqlalchemy import and_
from app import db
from app.models import Vehicle, Manufacturer, ScrapedInfo
from scraper.scrape_maker import scrape_manufacturer
from datetime import date

bp = Blueprint('routes', __name__)

@bp.route('/import_csv', methods=['POST'])
def import_csv():
    if request.method == 'GET':
        return render_template("import_csv.html")
    
    print("\n✅ /import_csv に到達")
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'CSVファイルが必要です'}), 400

    df = pd.read_csv(file, encoding='cp932')

    # ✅ 事前にスクレイピング済みIDを除外
    done_ids = db.session.query(Vehicle.internal_code).join(ScrapedInfo).filter(
        ScrapedInfo.manufacturer_name.notin_(["仮メーカー", "不明"])
    ).all()
    done_ids = [code[0] for code in done_ids]
    df = df[~df["自社管理番号"].isin(done_ids)]
    print(f"🧹 スクレイピング済みの {len(done_ids)} 件を除外しました → 処理対象: {len(df)} 件")

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
            print(f"⏸ {batch_size}件処理ごとに休憩中...")
            time.sleep(10)

        vehicle = Vehicle.query.filter_by(internal_code=row.自社管理番号).first()
        if not vehicle:
            vehicle = Vehicle(
                car_name=row.車名,
                model_code=row.認定型式,
                year=row.年式,
                internal_code=row.自社管理番号
            )
            db.session.add(vehicle)
            print(f"📝 Vehicle 追加: {row.自社管理番号}")
        else:
            print(f"📦 Vehicle 既に存在: {row.自社管理番号}")

        scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()
        # ✅ すでに仮メーカー以外が登録されていたらスキップ
        if scraped and scraped.manufacturer_name not in ["仮メーカー", "不明"]:
            print(f"⏭ スクレイピング済みスキップ: {row.自社管理番号}")
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
                scraped.retrieved_date = date.today()
                scraped.source_url = "https://www.kurumaerabi.com/"
                print("♻️ 仮メーカーを不明に更新")
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
            scraped.retrieved_date = date.today()
            scraped.source_url = "https://www.kurumaerabi.com/"
            print(f"♻️ スクレイピング情報を更新: {row.自社管理番号}")
        else:
            scraped_info = ScrapedInfo(
                vehicle=vehicle,
                manufacturer_name=maker_name,
                model_spec="取得予定",
                retrieved_date=date.today(),
                source_url="https://www.kurumaerabi.com/"
            )
            db.session.add(scraped_info)
            print(f"🧾 スクレイピング情報追加: {row.自社管理番号}")

        added += 1

    # ✅ CSVとして失敗IDを出力
    if fail_ids:
        with open("failed_ids.csv", mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["internal_code"])
            for code in fail_ids:
                writer.writerow([code])
        print(f"📄 失敗データをfailed_ids.csvに書き出しました（{len(fail_ids)}件）")

    db.session.commit()
    return jsonify({
        'message': f'{added} 件の車両を登録しました',
        'success_count': success_count,
        'fail_count': len(fail_ids),
        'fail_file': "failed_ids.csv" if fail_ids else None
    })


# ✅ 一覧表示 & キーワード絞り込みルート
@bp.route("/vehicles", methods=["GET"])
def list_vehicles():
    keyword = request.args.get("keyword", "").strip()
    query = Vehicle.query

    if keyword:
        query = query.filter(
            or_(
                Vehicle.car_name.ilike(f"%{keyword}%"),
                Vehicle.model_code.ilike(f"%{keyword}%"),
                Vehicle.internal_code.ilike(f"%{keyword}%")
            )
        )

    vehicles = query.order_by(Vehicle.id.desc()).all()
    return render_template("vehicle_list.html", vehicles=vehicles, keyword=keyword)
