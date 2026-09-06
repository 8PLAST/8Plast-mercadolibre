from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from db import Database


ROOT = Path(__file__).resolve().parent
REAL_DB = ROOT / "8plast_stock.db"
TESTS = [
    (19, "MLA3326800390", 1),
    (30, "MLA3342742490", 1),
    (11, "MLA2896751378", 1),
]


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stock_snapshot(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [tuple(r) for r in con.execute("SELECT id,sku,stock FROM products ORDER BY id")]
    finally:
        con.close()


before_hash = file_hash(REAL_DB)
before_stocks = stock_snapshot(REAL_DB)

with tempfile.TemporaryDirectory(prefix="8plast_stock_sim_") as temp_dir:
    temp_db = Path(temp_dir) / "simulation.db"
    source = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    target = sqlite3.connect(temp_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    sim = Database(temp_db)
    results = []
    for excel_row, listing_id, sale_quantity in TESTS:
        with sim.session() as con:
            association = con.execute("""SELECT ml.*,p.sku,p.name product_name,p.stock
                FROM marketplace_listings ml JOIN products p ON p.id=ml.product_id
                WHERE ml.marketplace='MERCADOLIBRE' AND ml.listing_id=? AND ml.variation_id=''""",
                (listing_id,)).fetchone()
        if not association:
            raise RuntimeError(f"No existe asociación para {listing_id}")
        stock_before = association["stock"]
        expected_discount = association["units_consumed"] * sale_quantity
        order = {
            "id": f"SIM-{excel_row}-{datetime.now().timestamp()}",
            "status": "paid",
            "date_created": datetime.now().astimezone().isoformat(),
            "order_items": [{"item": {"id": listing_id}, "quantity": sale_quantity}],
        }
        processed = sim.process_marketplace_order(order)
        with sim.session() as con:
            stock_after = con.execute("SELECT stock FROM products WHERE id=?", (association["product_id"],)).fetchone()[0]
        results.append({
            "excel_row": excel_row,
            "listing_id": listing_id,
            "product_id": association["product_id"],
            "sku": association["sku"],
            "product_name": association["product_name"],
            "stock_initial": stock_before,
            "units_discounted": expected_discount,
            "stock_resulting": stock_after,
            "processed": processed["processed"],
            "correct": stock_after == stock_before - expected_discount,
        })

    shared = [r for r in results if r["excel_row"] in (19, 30)]
    simulation_report = {
        "tests": results,
        "shared_product_same_id": len(shared) == 2 and shared[0]["product_id"] == shared[1]["product_id"],
        "shared_product_same_sku": len(shared) == 2 and shared[0]["sku"] == shared[1]["sku"],
        "shared_final_stock": shared[-1]["stock_resulting"],
        "all_correct": all(r["correct"] and r["processed"] == 1 for r in results),
    }

after_hash = file_hash(REAL_DB)
after_stocks = stock_snapshot(REAL_DB)
simulation_report.update({
    "temporary_copy_deleted": not Path(temp_dir).exists(),
    "real_db_hash_before": before_hash,
    "real_db_hash_after": after_hash,
    "real_db_file_unchanged": before_hash == after_hash,
    "real_stocks_unchanged": before_stocks == after_stocks,
    "real_product_count_before": len(before_stocks),
    "real_product_count_after": len(after_stocks),
})
print(json.dumps(simulation_report, ensure_ascii=False, indent=2))
