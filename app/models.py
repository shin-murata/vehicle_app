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

    model_code_id = db.Column(db.Integer, db.ForeignKey('model_codes.id'))  # 外部キー追加
    model_code_obj = db.relationship('ModelCode', back_populates='vehicles')  # 関連付け

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
    retrieved_date = db.Column(db.DateTime)          # 取得日
    source_url = db.Column(db.String)            # 取得元URL

    vehicle = db.relationship('Vehicle', back_populates='scraped_info')

# 新しいモデル
class Estimation(db.Model):
    __tablename__ = 'estimations'

    id = db.Column(db.Integer, primary_key=True)
    maker = db.Column(db.String)          # メーカー（手入力も可）
    car_name = db.Column(db.String)        # 車名
    model_code = db.Column(db.String)      # 型式
    owner = db.Column(db.String)           # 持ち主（値付け先）← NEW
    estimate_price = db.Column(db.Integer) # 査定価格（見積もり金額）
    sale_price = db.Column(db.Integer)     # 販売価格 ← NEW
    buyer = db.Column(db.String)           # 販売先 ← NEW
    sold_at = db.Column(db.DateTime)        # 販売日 ← NEW
    note = db.Column(db.String)             # 備考 ← NEW
    estimated_at = db.Column(db.DateTime, default=datetime.utcnow)  # 自動記録

    model_code_id = db.Column(db.Integer, db.ForeignKey('model_codes.id'))  # 外部キー追加
    model_code_obj = db.relationship('ModelCode', back_populates='vehicles')  # 関連付け

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
    name = db.Column(db.String, unique=True, nullable=False)  # 型式（例：MH21Sなど）

    # Vehicleとのリレーション（あとで追加）
    vehicles = db.relationship('Vehicle', back_populates='model_code_obj')
