# reimport_missing.py
import pandas as pd
from app import app, db  # FlaskアプリとDBを読み込み
from models import Vehicle  # ← 修正ポイント！

csv_path = "missing_5.csv"  # CSVファイル名（同じフォルダに置く）

df = pd.read_csv(csv_path, encoding="utf-8-sig")

with app.app_context():
    for _, row in df.iterrows():
        intake_number = str(row["入庫番号"])
        existing = Vehicle.query.filter_by(intake_number=intake_number).first()
        if existing:
            print(f"⚠️ 既に存在: {intake_number}")
            continue

        v = Vehicle(
            intake_number=intake_number,
            car_name=row.get("車名"),
            client=row.get("依頼元"),
            manufacturer_id=None,
        )
        db.session.add(v)
        print(f"✅ 登録: {intake_number}")

    db.session.commit()
    print("🎉 5件の登録が完了しました。")
