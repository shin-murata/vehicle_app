# scraper/test_scrape.py

import sys
import os

# ✅ vehicle_app のルートディレクトリをモジュール検索パスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# test_scrape.py
from scraper.scrape_maker import scrape_manufacturer

if __name__ == "__main__":
    car_name = "ワゴンR"
    model_code = "MH21S"
    result = scrape_manufacturer(car_name, model_code)
    print("✅ スクレイピング結果:", result)