from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from db import DB_PATH, Database, now_text
from ml_integration import MercadoLibreClient


root=Path(__file__).resolve().parent
stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
backup=root/f"8plast_stock_pre_activacion_ml_{stamp}.db"

source=sqlite3.connect(str(DB_PATH),timeout=15)
target=sqlite3.connect(str(backup))
try:
    source.backup(target)
finally:
    target.close(); source.close()

db=Database(DB_PATH)
activation_at=now_text()
db.configure_marketplace_processing(True,300,activation_at)
with db.session() as con:
    movements_before=con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    stock_before=con.execute("SELECT SUM(stock) FROM products").fetchone()[0]

error=None; summary=None
try:
    summary=MercadoLibreClient(db).sync_recent_orders()
except Exception as exc:
    error=f"{type(exc).__name__}: {exc}"

with db.session() as con:
    movements_after=con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    stock_after=con.execute("SELECT SUM(stock) FROM products").fetchone()[0]
    pending=con.execute("SELECT COUNT(*) FROM marketplace_order_reviews WHERE status='PENDIENTE'").fetchone()[0]
    config=dict(con.execute("SELECT * FROM marketplace_processing_config WHERE id=1").fetchone())

print(json.dumps({"backup":str(backup),"activation_at":activation_at,"config":config,
                  "first_query":summary,"error":error,"movements_created":movements_after-movements_before,
                  "stock_before":stock_before,"stock_after":stock_after,"pending_reviews_total":pending},
                 ensure_ascii=False,indent=2))
