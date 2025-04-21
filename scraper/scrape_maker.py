import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus  # URLエンコード：スペース→+、日本語→%xx形式
import unicodedata                   # Unicode正規化のため
import re                            # 正規表現で接頭辞除去に使用

# scraper/scrape_maker.py
# ここではキーワード生成とスクレイピング処理に集中します

def scrape_manufacturer(car_name, model_code):
    # str化して安全に扱う
    car_name = str(car_name)
    model_code = str(model_code)

    # 全角⇄半角などを統一（NFKC）し、車名の「・」を除去
    car_name_normalized = unicodedata.normalize("NFKC", car_name).replace("・", "")
    model_code_normalized = unicodedata.normalize("NFKC", model_code)

    # 型式の先頭に付く接頭辞（CBA-／DBA-など）を削除
    model_code_cleaned = re.sub(r"^[A-Z]+-", "", model_code_normalized)
    print(f"🔧 型式前処理: {model_code_normalized} → {model_code_cleaned}")

    # 検索用キーワード生成
    keyword = f"{car_name_normalized} {model_code_cleaned}"
    print(f"🔍 正規化後キーワード: {keyword}")

    # キーワードをUTF-8でURLエンコード
    encoded_keyword = quote_plus(keyword)

    # 検索先URL（kurumaerabi向け）
    url = f"https://www.kurumaerabi.com/search/?q={encoded_keyword}&btnsubmit=検索"

    headers = {
        # 人間らしいブラウザヘッダに設定
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/113.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        print(f"🌐 HTTPステータス: {res.status_code}, URL: {res.url}")
        print(res.text[:500])  # HTML先頭500文字を表示

        if res.status_code != 200:
            return "不明"

        soup = BeautifulSoup(res.content, "html.parser")

        # <div class="car_maker_name"><p>メーカー名</p></div> を取得
        maker_div = soup.find("div", class_="car_maker_name")
        if maker_div and maker_div.find("p"):
            maker_name = maker_div.find("p").text.strip()
            print(f"✅ メーカー名取得: {maker_name}")
            return maker_name
        else:
            print("❌ メーカー名が見つかりませんでした")
            return "不明"

    except Exception as e:
        print(f"❌ スクレイピング中にエラー発生: {e}")
        return "不明"
