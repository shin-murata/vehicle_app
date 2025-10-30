# worker.py
import os
from redis import Redis
from rq import Connection, Worker

# あなたのアプリ初期化に合わせて import
try:
    from app import create_app
    app = create_app()
except Exception:
    from app import app  # create_app が無い構成ならこちら

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
conn = Redis.from_url(redis_url)

if __name__ == "__main__":
    with app.app_context():
        with Connection(conn):
            Worker(['default']).work()
