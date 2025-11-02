# run.py

from app import create_app
from dotenv import load_dotenv
import os

load_dotenv()

app = create_app()

if __name__ == "__main__":
    # 置き換え
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")

