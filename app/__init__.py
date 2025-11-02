# app/__init__.py

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config
import os
from dotenv import load_dotenv

load_dotenv()


db = SQLAlchemy()
migrate = Migrate()

def create_app(config_class=Config):
    app = Flask(__name__, static_folder="../static")
    app.config.from_object(config_class)

    # ✅ ここで環境変数から上書き
    app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    )
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)

    db.init_app(app)
    migrate.init_app(app, db)

    from app import models  # ✅ これを忘れると migrate できません

    # ✅ Blueprint を登録（追加する）
    from app.routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    return app
