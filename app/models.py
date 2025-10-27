from app import db
from datetime import datetime

# 🚘 Vehicle table (Main vehicle information)
class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    intake_number = db.Column(db.Integer)  # 入庫番号
    status = db.Column(db.String)          # ステータス
    condition = db.Column(db.String)       # 状態
    pickup_date = db.Column(db.Date)       # 引取完了日
    client = db.Column(db.String)          # 依頼元
    car_name = db.Column(db.String)        # 車名
    model_code = db.Column(db.String)      # 認定型式
    year = db.Column(db.Integer)           # 年式
    vin = db.Column(db.String)             # 車台番号
    color = db.Column(db.String)           # 車色
    estimate_price = db.Column(db.Integer) # 見積金額
    internal_code = db.Column(db.String)   # 自社管理番号

    manufacturer_id = db.Column(db.Integer, db.ForeignKey('manufacturers.id'))
    manufacturer = db.relationship('Manufacturer', back_populates='vehicles')

    model_code_id = db.Column(db.Integer, db.ForeignKey('model_codes.id'))
    # Vehicle <-> ModelCode のペア
    model_code_obj = db.relationship('ModelCode', back_populates='vehicles')

    scraped_info = db.relationship('ScrapedInfo', back_populates='vehicle', cascade='all, delete-orphan')

# 🏭 Manufacturer master table
class Manufacturer(db.Model):
    __tablename__ = 'manufacturers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

    vehicles = db.relationship('Vehicle', back_populates='manufacturer')


# 📄 Scraping history table
class ScrapedInfo(db.Model):
    __tablename__ = 'scraped_info'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'))
    manufacturer_name = db.Column(db.String)     # メーカー
    model_spec = db.Column(db.String)            # 型式別スペック
    retrieved_date = db.Column(db.DateTime)      # 取得日
    source_url = db.Column(db.String)            # 取得元URL

    vehicle = db.relationship('Vehicle', back_populates='scraped_info')


# 新しいモデル
class Estimation(db.Model):
    __tablename__ = 'estimations'

    id = db.Column(db.Integer, primary_key=True)
    maker = db.Column(db.String)
    car_name = db.Column(db.String)
    model_code = db.Column(db.String)
    owner = db.Column(db.String)
    estimate_price = db.Column(db.Integer)
    sale_price = db.Column(db.Integer)
    buyer = db.Column(db.String)
    sold_at = db.Column(db.DateTime)
    note = db.Column(db.String)
    estimated_at = db.Column(db.DateTime, default=datetime.utcnow)

    model_code_id = db.Column(db.Integer, db.ForeignKey('model_codes.id'))
    # Estimation <-> ModelCode のペア
    model_code_obj = db.relationship('ModelCode', back_populates='estimations')


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)


class Buyer(db.Model):
    __tablename__ = 'buyers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)


# 認定型式マスターテーブル
class ModelCode(db.Model):
    __tablename__ = 'model_codes'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

    # ModelCode 側のリレーションペア（それぞれ対応する back_populates 名と一致）
    vehicles = db.relationship('Vehicle', back_populates='model_code_obj')
    estimations = db.relationship('Estimation', back_populates='model_code_obj')