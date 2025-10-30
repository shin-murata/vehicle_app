# tasks/import_job.py
import os
import csv
import re
import gc
import time
import unicodedata
from datetime import datetime, timezone, timedelta

import pandas as pd
from sqlalchemy import and_

from app import db
from app.models import Vehicle, Manufacturer, ScrapedInfo
from scraper.scrape_maker import scrape_manufacturer

# JST
JST = timezone(timedelta(hours=9))

def _set_progress(job, **kwargs):
    try:
        meta = job.meta or {}
        meta.update(kwargs)
        job.meta = meta
        job.save_meta()
    except Exception:
        pass

def process_csv_and_scrape(csv_path: str, resume: bool):
    # RQ の現在ジョブ
    try:
        from rq import get_current_job
        job = get_current_job()
    except Exception:
        job = None

    def to_zenkaku(text):
        if isinstance(text, str):
            return unicodedata.normalize('NFKC', text)
        return text

    USECOLS = [
        "入庫番号", "ステータス", "状態", "引取完了日", "依頼元",
        "車名", "認定型式", "年式", "車台番号", "車色",
        "見積金額", "自社管理番号"
    ]

    CHUNK = 50
    batch_size = 20
    sleep_seconds = 2  # レート制御はワーカー側ならOK

    RESUME_LOG_PATH = os.path.join("static", "processed_intake_numbers.txt")
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

    added = 0
    fail_ids = []
    success_count = 0
    processed = 0
    seen_in_csv = set()
    to_log_after_commit: list[int] = []

    def flush_resume_log(ids: list[int]):
        if not ids:
            return
        os.makedirs(os.path.dirname(RESUME_LOG_PATH), exist_ok=True)
        with open(RESUME_LOG_PATH, "a") as f:
            for v in ids:
                f.write(f"{v}\n")
        ids.clear()

    chunk_iter = pd.read_csv(
        csv_path,
        encoding='cp932',
        dtype=str,
        chunksize=CHUNK,
        usecols=USECOLS
    )

    for df in chunk_iter:
        try:
            for row in df.itertuples():
                raw_intake = getattr(row, "入庫番号", None)
                raw_code   = getattr(row, "自社管理番号", None)

                if job:
                    _set_progress(job, message=f"処理中: {raw_code}")

                key = None
                if raw_intake is not None and str(raw_intake).strip() != "" and not pd.isna(raw_intake):
                    try:
                        key = int(str(raw_intake).strip())
                    except ValueError:
                        key = None
                if key is None:
                    continue

                if resume and key in processed_ids_from_log:
                    to_log_after_commit.append(key); continue
                if key in seen_in_csv:
                    continue
                seen_in_csv.add(key)

                raw_model = getattr(row, "認定型式", None)
                if (raw_model is None) or pd.isna(raw_model) or str(raw_model).strip() == "":
                    continue

                processed += 1
                if processed % batch_size == 0:
                    try:
                        db.session.commit()
                        db.session.expunge_all()
                        gc.collect()
                        flush_resume_log(to_log_after_commit)
                    except Exception:
                        db.session.rollback()
                    time.sleep(sleep_seconds)
                    if job:
                        _set_progress(job, processed=processed, success=success_count, failed=len(fail_ids))

                # Vehicle 取得/作成
                vehicle = Vehicle.query.filter_by(intake_number=key).first()
                if vehicle:
                    scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()
                    if vehicle.manufacturer_id or (scraped and scraped.manufacturer_name not in ["仮メーカー", "不明"]):
                        to_log_after_commit.append(key)
                        continue

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
                else:
                    if vehicle.status is None: vehicle.status = nz_str(getattr(row, "ステータス", None))
                    if vehicle.condition is None: vehicle.condition = nz_str(getattr(row, "状態", None))
                    if vehicle.pickup_date is None: vehicle.pickup_date = nz_str(getattr(row, "引取完了日", None))
                    if vehicle.client is None: vehicle.client = nz_str(getattr(row, "依頼元", None))
                    if vehicle.car_name is None: vehicle.car_name = nz_str(getattr(row, "車名", None))
                    if vehicle.model_code is None: vehicle.model_code = nz_str(raw_model)
                    if vehicle.year is None: vehicle.year = nz_str(getattr(row, "年式", None))
                    if vehicle.vin is None: vehicle.vin = nz_str(getattr(row, "車台番号", None))
                    if vehicle.color is None: vehicle.color = nz_str(getattr(row, "車色", None))
                    if vehicle.estimate_price is None: vehicle.estimate_price = nz_str(getattr(row, "見積金額", None))
                    db.session.add(vehicle)

                scraped = ScrapedInfo.query.filter_by(vehicle_id=vehicle.id).first()
                if scraped and scraped.manufacturer_name in ["仮メーカー", "不明"]:
                    to_log_after_commit.append(key); continue

                # 既存ScrapedInfo の再利用 or スクレイピング
                existing_info = ScrapedInfo.query.filter(
                    ScrapedInfo.manufacturer_name.notin_(["不明", "仮メーカー"]),
                    ScrapedInfo.vehicle.has(
                        and_(Vehicle.car_name == getattr(row, "車名", None),
                             Vehicle.model_code == raw_model)
                    )
                ).first()

                if existing_info:
                    maker_name = existing_info.manufacturer_name
                else:
                    time.sleep(sleep_seconds)
                    maker_name = scrape_manufacturer(getattr(row, "車名", None), raw_model)

                if maker_name == "不明":
                    fail_ids.append(str(getattr(row, "自社管理番号", "")) or "")
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
                    to_log_after_commit.append(key)
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
                else:
                    db.session.add(ScrapedInfo(
                        vehicle=vehicle,
                        manufacturer_name=maker_name,
                        model_spec="取得予定",
                        retrieved_date=datetime.now(JST).date(),
                        source_url="https://www.kurumaerabi.com/"
                    ))

                added += 1
                to_log_after_commit.append(key)

            db.session.commit()
            db.session.expunge_all()
            gc.collect()
            flush_resume_log(to_log_after_commit)
            del df
            gc.collect()

            if job:
                _set_progress(job, processed=processed, success=success_count, failed=len(fail_ids))

        except Exception as e:
            db.session.rollback()
            try:
                flush_resume_log(to_log_after_commit)
            except Exception:
                pass
            if job:
                _set_progress(job, message=f"チャンク処理中エラー: {e}")

    if fail_ids:
        fail_filename = "failed_ids.csv"
        fail_path = os.path.join("static", fail_filename)
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["internal_code"])
            for code in fail_ids:
                writer.writerow([code])

    db.session.commit()

    if job:
        _set_progress(job, state="finished", processed=processed, success=success_count, failed=len(fail_ids))
    return {"added": added, "success": success_count, "failed": len(fail_ids)}

# --- ここから追記（tasks/import_job.py の末尾に） ---
import tempfile
from rq import get_current_job

def run(file_bytes: bytes, resume: bool = False):
    """
    /import_csv から渡ってくる CSV バイト列を一時ファイルに保存し、
    既存の本処理 process_csv_and_scrape(csv_path, resume) を呼び出すだけの“つなぎ役”。
    """
    job = None
    try:
        job = get_current_job()
    except Exception:
        pass

    # 初期メタ
    if job:
        _set_progress(job, progress=0, processed=0, success=0, failed=0, message="CSV 受信")

    tmp_path = None
    try:
        # /tmp に一時CSVを書き出し
        fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir="/tmp")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        if job:
            _set_progress(job, message="CSV保存完了 → 解析開始")

        # 既存の本処理を呼び出す（ここがあなたのロジック）
        result = process_csv_and_scrape(tmp_path, resume)

        if job:
            _set_progress(job, progress=100, message="完了")
        return result

    except Exception as e:
        # 失敗時は RQ 側の status=failed になります。理由は /jobs/<id>/status の error で見えるようにしてある。
        if job:
            _set_progress(job, message=f"run() 失敗: {e}")
        raise

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
# --- 追記ここまで ---
