# scripts/register_clients_and_buyers.py

from dotenv import load_dotenv
load_dotenv()

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models import Vehicle, Estimation, Client, Buyer

app = create_app()

with app.app_context():
    # ✅ Vehicleから依頼元（client）を収集
    distinct_clients = db.session.query(Vehicle.client).distinct().all()
    for (client_name,) in distinct_clients:
        if client_name and not Client.query.filter_by(name=client_name).first():
            db.session.add(Client(name=client_name))

    # ✅ Estimationから販売先（buyer）を収集
    distinct_buyers = db.session.query(Estimation.buyer).distinct().all()
    for (buyer_name,) in distinct_buyers:
        if buyer_name and not Buyer.query.filter_by(name=buyer_name).first():
            db.session.add(Buyer(name=buyer_name))

    db.session.commit()
    print("✅ Vehicle.client と Estimation.buyer からマスター登録完了")
