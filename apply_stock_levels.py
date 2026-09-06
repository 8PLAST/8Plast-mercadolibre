from __future__ import annotations

import json
import sqlite3

from db import DB_PATH, now_text


LEVELS = {
    "CON-NEG-90120-35": (1000,3000), "CON-NEG-80110-35": (1000,2500),
    "CON-NEG-6090-35": (1000,2500), "CON-NEG-5070-35": (1000,2500),
    "CON-NEG-4560-35": (1000,2000), "CON-VER-80110-35": (1000,2000),
    "CON-VER-90120-35": (1000,3000), "CON-VER-70100-35": (1000,3000),
    "CON-VER-4560-35": (500,1500), "CON-VER-4050-35": (500,1500),
    "CON-VER-5070-35": (500,1500), "CON-ROJ-5070-35": (1000,2500),
    "CON-ROJ-6090-35": (1000,2500), "CON-ROJ-80110-35": (750,1500),
    "CON-ROJ-90120-35": (1000,2000), "CON-ROJ-90120-100": (400,1500),
    "CON-ROJ-6090-100": (500,1500), "CON-AMA-6090-35": (500,1500),
    "CON-AMA-90120-35": (500,1500), "CON-AMA-6090-100": (500,2000),
    "CON-AMA-90120-100": (500,2000), "LENA-4070-60": (1500,5000),
    "LENA-4060-60": (1500,5000), "LENA-5080-60": (1500,5000),
    "LENA-3063-60": (1500,5000), "CARB-4070-60": (1000,3000),
    "CRI-4060-50": (1000,5000), "CRI-2530-50": (1000,3000),
    "CRI-2535-50": (1000,3000), "CRI-2540-50": (1000,3000),
    "CRI-2550-50": (1000,3000), "CRI-3040-50": (1000,3000),
    "CRI-3050-50": (1000,3000), "CRI-3540-50": (700,2000),
    "CRI-3545-50": (700,2000), "CRI-3550-50": (700,2000),
    "CRI-2030-50": (700,2500), "CRI-1520-70": (1000,3000),
    "CRI-1525-70": (1000,3000), "CRI-1530-70": (1000,3000),
    "CRI-4050-60": (500,2000), "CON-AZU-80110-35": (600,1500),
    "CON-AZU-6090-35": (600,1500), "ESC-NEG-5070-100": (750,2500),
    "ESC-NEG-4060-80": (400,1500),
    "ROL-TRA-20-50-100": (7,20), "ROL-TRA-25-50-100": (10,25),
    "ROL-TRA-30-50-100": (6,15), "ROL-TRA-35-50-100": (6,15),
    "ROL-TRA-40-50-100": (8,15), "ROL-TRA-45-50-100": (4,10),
    "ROL-TRA-60-50-100": (10,20), "ROL-TRA-70-50-100": (5,10),
    "ROL-TRA-80-50-100": (5,10), "ROL-TRA-100-50-100": (5,10),
    "ROL-TRA-120-50-100": (4,10), "ROL-TRA-15-70-100": (5,15),
    "ROL-TRA-15-70-120": (5,15), "ROL-TRA-30-70-100": (3,10),
    "ROL-TRA-30-70-200": (3,10), "ROL-TRA-40-70-100": (3,6),
    "ROL-TRA-45-70-100": (3,6), "ROL-TRA-50-70-100": (5,10),
    "ROL-TRA-60-70-100": (3,7),
}


def main():
    con=sqlite3.connect(DB_PATH,timeout=30)
    con.row_factory=sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        before_stock=con.execute("SELECT SUM(stock) FROM products").fetchone()[0]
        before_movements=con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        before_associations=[tuple(r) for r in con.execute("""SELECT id,listing_id,variation_id,product_id,
            units_consumed,active,sync_mode FROM marketplace_listings ORDER BY id""")]
        before_packs=[tuple(r) for r in con.execute("SELECT id,storage_pack FROM products ORDER BY id")]
        existing={r["sku"]:r["id"] for r in con.execute("SELECT id,sku FROM products")}
        missing=sorted(set(LEVELS)-set(existing))
        if missing: raise RuntimeError(f"Faltan SKU: {missing}")
        for sku,(minimum,target) in LEVELS.items():
            con.execute("UPDATE products SET minimum_stock=?,target_stock=? WHERE sku=?",(minimum,target,sku))
        configured=con.execute("SELECT COUNT(*) FROM products WHERE minimum_stock>0 AND target_stock>0").fetchone()[0]
        partial=con.execute("SELECT COUNT(*) FROM products WHERE (minimum_stock=0)<>(target_stock=0)").fetchone()[0]
        unconfigured=con.execute("SELECT COUNT(*) FROM products WHERE minimum_stock=0 AND target_stock=0").fetchone()[0]
        errors=[]
        for sku,(minimum,target) in LEVELS.items():
            row=con.execute("SELECT minimum_stock,target_stock FROM products WHERE sku=?",(sku,)).fetchone()
            if tuple(row)!=(minimum,target): errors.append(sku)
        after_stock=con.execute("SELECT SUM(stock) FROM products").fetchone()[0]
        after_movements=con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        after_associations=[tuple(r) for r in con.execute("""SELECT id,listing_id,variation_id,product_id,
            units_consumed,active,sync_mode FROM marketplace_listings ORDER BY id""")]
        after_packs=[tuple(r) for r in con.execute("SELECT id,storage_pack FROM products ORDER BY id")]
        if configured!=64 or unconfigured!=25 or partial or errors: raise RuntimeError("Validación de niveles fallida")
        if before_stock!=after_stock or before_movements!=after_movements: raise RuntimeError("Stock o movimientos cambiaron")
        if before_associations!=after_associations or before_packs!=after_packs: raise RuntimeError("Asociaciones o packs cambiaron")
        con.commit()
        print(json.dumps({"configured":configured,"unconfigured":unconfigured,"errors":errors,
            "stock_before":before_stock,"stock_after":after_stock,"movements":after_movements,
            "associations":len(after_associations),"applied_at":now_text()},ensure_ascii=False))
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
