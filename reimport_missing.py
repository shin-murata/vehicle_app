import os
import csv
import psycopg2

CSV_PATH = os.path.expanduser("~/Projects/vehicle_app/missing_5.csv")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("ERROR: 環境変数 DATABASE_URL が未設定です。`.env` を読み込んでから実行してください。")

# CSVの想定ヘッダ:
#   入庫番号, 車名, 依頼元, ...（他列があってもOK）
# ある列だけ使ってINSERTします（他はNULLでOK）
USE_COLS = {
    "入庫番号": "intake_number",
    "車名": "car_name",
    "依頼元": "client",
}

def main():
    # CSVロード
    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("CSVにデータ行がありません。")
        return

    # DB接続
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for r in rows:
        intake_number = str(r.get("入庫番号", "")).strip()
        car_name = (r.get("車名") or "").strip()
        client = (r.get("依頼元") or "").strip()

        if not intake_number:
            print("⚠️ 入庫番号が空の行をスキップ:", r)
            skipped += 1
            continue

        # 既存チェック（intake_numberで存在確認）
        cur.execute("SELECT 1 FROM vehicles WHERE intake_number = %s LIMIT 1;", (intake_number,))
        exists = cur.fetchone() is not None
        if exists:
            print(f"↩️ 既存のためスキップ: {intake_number}")
            skipped += 1
            continue

        # 最小フィールドでINSERT（他カラムはNULL可）
        cur.execute("""
            INSERT INTO vehicles (intake_number, car_name, client)
            VALUES (%s, %s, %s)
        """, (intake_number, car_name, client))
        inserted += 1
        print(f"✅ 追加: {intake_number} / {car_name} / {client}")

    conn.commit()
    cur.close()
    conn.close()

    print("\n== 結果 ==")
    print(f"  追加:   {inserted} 件")
    print(f"  スキップ: {skipped} 件")

if __name__ == "__main__":
    main()
