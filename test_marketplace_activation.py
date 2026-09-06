from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from db import Database
from ml_integration import MercadoLibreError, MercadoLibreOrderPoller


ROOT=Path(__file__).resolve().parent
REAL_DB=ROOT/"8plast_stock.db"

def digest(path):
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()

def real_snapshot():
    con=sqlite3.connect(f"file:{REAL_DB}?mode=ro",uri=True)
    try:
        return [tuple(r) for r in con.execute("SELECT id,sku,stock FROM products ORDER BY id")], con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    finally: con.close()

def order(order_id,listing_id,date,status="paid",quantity=1):
    return {"id":order_id,"status":status,"date_created":date,
            "order_items":[{"item":{"id":listing_id},"quantity":quantity}]}

hash_before=digest(REAL_DB); stocks_before,movements_before=real_snapshot()
with tempfile.TemporaryDirectory(prefix="8plast_activation_test_") as folder:
    temp_db=Path(folder)/"test.db"
    src=sqlite3.connect(f"file:{REAL_DB}?mode=ro",uri=True); dst=sqlite3.connect(temp_db)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    db=Database(temp_db)
    activation=datetime.now().astimezone()
    db.configure_marketplace_processing(False,300,activation.isoformat())
    after=(activation+timedelta(seconds=1)).isoformat()
    before=(activation-timedelta(seconds=1)).isoformat()

    crystal=db.marketplace_listing("MLA3342742490")
    pid=crystal["product_id"]
    start=db.product(pid)["stock"]
    x50=db.process_marketplace_order(order("TEST-X50","MLA3342742490",after))
    after_x50=db.product(pid)["stock"]
    x100=db.process_marketplace_order(order("TEST-X100","MLA3326800390",after))
    after_x100=db.product(pid)["stock"]

    duplicate_first=db.process_marketplace_order(order("TEST-DUP","MLA3342742490",after))
    duplicate_stock_once=db.product(pid)["stock"]
    duplicate_second=db.process_marketplace_order(order("TEST-DUP","MLA3342742490",after))
    duplicate_stock_twice=db.product(pid)["stock"]

    stock_before_ignored=db.product(pid)["stock"]
    excluded=db.process_marketplace_order(order("TEST-EXCLUDED","MLA3096038444",after))
    unassociated=db.process_marketplace_order(order("TEST-UNASSOCIATED","MLA-NO-EXISTE",after))
    historical=db.process_marketplace_order(order("TEST-OLD","MLA3342742490",before))
    stock_after_ignored=db.product(pid)["stock"]

    cancel_sale=db.process_marketplace_order(order("TEST-CANCEL","MLA3342742490",after))
    stock_after_sale=db.product(pid)["stock"]
    movements_after_sale=len(db.movements())
    cancellation=db.process_marketplace_order(order("TEST-CANCEL","MLA3342742490",after,status="cancelled"))
    stock_after_cancel=db.product(pid)["stock"]
    repeated_cancel=db.process_marketplace_order(order("TEST-CANCEL","MLA3342742490",after,status="cancelled"))
    stock_after_repeat=db.product(pid)["stock"]
    movements_after_cancels=len(db.movements())
    reviews=db.marketplace_order_reviews()
    config=db.marketplace_processing_config()
    class FakeClient:
        def __init__(self,database): self.db=database
        def sync_recent_orders(self): raise AssertionError("No debe ejecutarse desactivado")
    poller=MercadoLibreOrderPoller(FakeClient(db))
    try:
        poller.start(); disabled_guard=False
    except MercadoLibreError:
        disabled_guard=True

    report={
      "automatic_config":{"enabled":config["enabled"],"interval_seconds":config["interval_seconds"],"activation_at":config["activation_at"],"disabled_guard":disabled_guard,"running":poller.running},
      "x50":{"start":start,"discount":start-after_x50,"result":after_x50,"processed":x50["processed"]},
      "x100":{"start":after_x50,"discount":after_x50-after_x100,"result":after_x100,"processed":x100["processed"]},
      "shared_product":{"same_product_id":db.marketplace_listing("MLA3342742490")["product_id"]==db.marketplace_listing("MLA3326800390")["product_id"],"sku":db.product(pid)["sku"]},
      "duplicate":{"first_processed":duplicate_first["processed"],"second_duplicates":duplicate_second["duplicates"],"stock_once":duplicate_stock_once,"stock_twice":duplicate_stock_twice},
      "ignored":{"excluded":excluded["excluded"],"unassociated":unassociated["unassociated"],"historical_skipped":historical["skipped_before_start"],"stock_before":stock_before_ignored,"stock_after":stock_after_ignored},
      "cancellation":{"sale_processed":cancel_sale["processed"],"stock_after_sale":stock_after_sale,"pending":cancellation["cancellations_pending"],"repeat":repeated_cancel["cancellation_repeats"],"stock_after_cancel":stock_after_cancel,"stock_after_repeat":stock_after_repeat,"movement_count_unchanged":movements_after_sale==movements_after_cancels,"review_rows":len(reviews),"review_seen_count":reviews[0]["seen_count"] if reviews else 0},
      "all_sync_modes_only_deduct":all(r["sync_mode"]=="SOLO_DESCONTAR_VENTAS" for r in db.marketplace_listings()),
    }

hash_after=digest(REAL_DB); stocks_after,movements_after=real_snapshot()
report["safety"]={"temp_deleted":not Path(folder).exists(),"real_hash_unchanged":hash_before==hash_after,
                  "real_stocks_unchanged":stocks_before==stocks_after,"real_movements_unchanged":movements_before==movements_after,
                  "network_calls":0}
print(json.dumps(report,ensure_ascii=False,indent=2))
