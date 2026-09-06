from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "8plast_stock.db"
BACKUP = sorted(ROOT.glob("8plast_stock_pre_config_manual_*.db"))[-1]
EXPECTED_NEW = {
    "CON-AZU-80110-35": 3000, "ESC-NEG-5070-100": 2500,
    "ROL-NEG-30-70-100": 5, "ROL-NEG-35-50-100": 5,
    "CON-AZU-6090-35": 2500, "CRI-4060-60": 1000,
    "ROL-TRA-40-70-100": 3, "ROL-TRA-30-50-400": 1,
    "ROL-TRA-30-70-200": 4, "CRI-5070-30": 900,
    "ESC-NEG-4060-80": 3000, "CRI-5080-30": 800,
}

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
con.execute("ATTACH DATABASE ? AS before", (str(BACKUP),))
out = {}
out["integrity_check"] = con.execute("PRAGMA integrity_check").fetchone()[0]
out["foreign_key_errors"] = [tuple(r) for r in con.execute("PRAGMA foreign_key_check")]
out["products"] = dict(con.execute("SELECT COUNT(*) total, SUM(kind='BAG') bags, SUM(kind='ROLL') rolls, SUM(stock) stock FROM products").fetchone())
out["movements"] = con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
out["associations"] = dict(con.execute("""SELECT COUNT(*) total,
    SUM(sync_mode='SOLO_DESCONTAR_VENTAS') only_deduct,
    SUM(sync_mode='SINCRONIZAR_STOCK') synchronize,
    COUNT(DISTINCT product_id) physical_products FROM marketplace_listings""").fetchone())
out["exclusions"] = con.execute("SELECT COUNT(*) FROM marketplace_listing_exclusions WHERE active=1").fetchone()[0]
out["association_exclusion_overlap"] = con.execute("""SELECT COUNT(*) FROM marketplace_listings ml
    JOIN marketplace_listing_exclusions ex ON ex.marketplace=ml.marketplace AND ex.listing_id=ml.listing_id
      AND ex.variation_id=ml.variation_id AND ex.active=1""").fetchone()[0]
out["old_product_stock_changes"] = [dict(r) for r in con.execute("""SELECT p.sku,b.stock old_stock,p.stock new_stock
    FROM products p JOIN before.products b ON b.sku=p.sku
    WHERE p.stock<>b.stock ORDER BY p.sku""")]
out["new_products"] = [dict(r) for r in con.execute("""SELECT p.sku,p.name,p.kind,p.width_cm,p.length_cm,p.microns,
    p.meters_per_roll,p.color_material,p.category,p.stock,
    COALESCE(SUM(CASE WHEN m.external_key='MANUAL-CONFIG:'||p.sku THEN m.quantity_delta ELSE 0 END),0) movement_stock
    FROM products p LEFT JOIN stock_movements m ON m.product_id=p.id
    WHERE p.sku IN (%s) GROUP BY p.id ORDER BY p.sku""" % ",".join("?"*len(EXPECTED_NEW)), tuple(EXPECTED_NEW))]
out["new_stock_expected"] = sum(EXPECTED_NEW.values())
out["new_stock_actual"] = sum(r["stock"] for r in out["new_products"])
out["new_stock_movement_total"] = sum(r["movement_stock"] for r in out["new_products"])
out["shared_crystal"] = [dict(r) for r in con.execute("""SELECT ml.listing_id,p.sku,p.stock,ml.units_consumed,ml.sync_mode
    FROM marketplace_listings ml JOIN products p ON p.id=ml.product_id
    WHERE ml.listing_id IN ('MLA3326800390','MLA3342742490') ORDER BY ml.listing_id""")]
out["excluded_22_24"] = [r[0] for r in con.execute("""SELECT listing_id FROM marketplace_listing_exclusions
    WHERE active=1 AND listing_id IN ('MLA1829368267','MLA3040488624','MLA1761653855') ORDER BY listing_id""")]
out["bad_units"] = [dict(r) for r in con.execute("SELECT listing_id,units_consumed FROM marketplace_listings WHERE units_consumed<=0")]
out["duplicate_physical"] = [dict(r) for r in con.execute("""SELECT kind,width_cm,COALESCE(length_cm,-1) length_cm,microns,
    COALESCE(meters_per_roll,-1) meters_per_roll,LOWER(color_material) color_material,LOWER(category) category,COUNT(*) count
    FROM products WHERE active=1 GROUP BY kind,width_cm,COALESCE(length_cm,-1),microns,
    COALESCE(meters_per_roll,-1),LOWER(color_material),LOWER(category) HAVING COUNT(*)>1""")]
print(json.dumps(out, ensure_ascii=False, indent=2))
