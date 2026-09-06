from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from db import Database


ROOT = Path(__file__).resolve().parent
REAL_DB = ROOT / "8plast_stock.db"
SALES = [
    {"sale_id":"2000018121989082","listing_id":"MLA1960666321","quantity":1,"date":"2026-08-25T23:23:00-03:00"},
    {"sale_id":"2000014690193843","listing_id":"MLA3745655034","quantity":1,"date":"2026-08-24T18:48:00-03:00"},
    {"sale_id":"2000014694059709","listing_id":"MLA3775653192","quantity":1,"date":"2026-08-24T22:33:00-03:00"},
    {"sale_id":"2000018113373654","listing_id":"MLA2812718186","quantity":1,"date":"2026-08-25T14:50:00-03:00"},
]

con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows=[]
for sale in SALES:
    r=con.execute("""SELECT ml.listing_id,ml.product_id,ml.units_consumed,ml.sync_from,ml.sync_mode,
        p.sku,p.name,p.stock FROM marketplace_listings ml JOIN products p ON p.id=ml.product_id
        WHERE ml.marketplace='MERCADOLIBRE' AND ml.listing_id=? AND ml.variation_id='' AND ml.active=1""",
        (sale["listing_id"],)).fetchone()
    if r:
        d=dict(r); d.update(sale); d["physical_discount"]=d["units_consumed"]*sale["quantity"]
        d["calculated_stock_result"]=d["stock"]-d["physical_discount"]
        d["before_sync_start"]=datetime.fromisoformat(sale["date"]) < datetime.fromisoformat(d["sync_from"])
        rows.append(d)
con.close()

with tempfile.TemporaryDirectory(prefix="8plast_recent_sale_test_") as temp_dir:
    temp_db=Path(temp_dir)/"simulation.db"
    src=sqlite3.connect(f"file:{REAL_DB}?mode=ro",uri=True); dst=sqlite3.connect(temp_db)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    sim=Database(temp_db)
    chosen=rows[0]
    synthetic_date=(datetime.fromisoformat(chosen["sync_from"])+timedelta(seconds=1)).isoformat()
    order={"id":chosen["sale_id"],"status":"paid","date_created":synthetic_date,
           "order_items":[{"item":{"id":chosen["listing_id"]},"quantity":chosen["quantity"]}]}
    first=sim.process_marketplace_order(order)
    second=sim.process_marketplace_order(order)
    with sim.session() as c:
        final_stock=c.execute("SELECT stock FROM products WHERE id=?",(chosen["product_id"],)).fetchone()[0]
        movement_count=c.execute("SELECT COUNT(*) FROM stock_movements WHERE external_key LIKE ?",(f"ML:SALE:{chosen['sale_id']}:%",)).fetchone()[0]
    idempotency={"listing_id":chosen["listing_id"],"stock_before":chosen["stock"],"stock_after_two_attempts":final_stock,
                 "discount_once":chosen["physical_discount"],"first":first,"second":second,"sale_movements":movement_count}

real=sqlite3.connect(f"file:{REAL_DB}?mode=ro",uri=True)
real_status={
    "stock_total":real.execute("SELECT SUM(stock) FROM products").fetchone()[0],
    "movements":real.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],
    "ml_sale_movements":real.execute("SELECT COUNT(*) FROM stock_movements WHERE external_key LIKE 'ML:SALE:%'").fetchone()[0],
}
real.close()
print(json.dumps({"matched_sales":rows,"idempotency_test":idempotency,
                  "temporary_copy_deleted":not Path(temp_dir).exists(),"real_database":real_status},
                 ensure_ascii=False,indent=2))
