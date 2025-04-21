# config.py

import os

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")  # ← SQLiteの代替削除！
    SQLALCHEMY_TRACK_MODIFICATIONS = False
