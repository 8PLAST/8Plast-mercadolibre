from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from db import now_text


ROOT = Path(__file__).resolve().parent
DB = ROOT / "8plast_stock.db"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"8plast_stock_pre_config_manual_{STAMP}.db"

NEW_PRODUCTS = [
    dict(kind="BAG", sku="CON-AZU-80110-35", name="Bolsa consorcio azul 80x110 35 mic", width_cm=80, length_cm=110, microns=35, meters_per_roll=None, color_material="Azul", category="Consorcio", stock=3000, storage_pack=50),
    dict(kind="BAG", sku="ESC-NEG-5070-100", name="Bolsa para escombro negra 50x70 100 mic", width_cm=50, length_cm=70, microns=100, meters_per_roll=None, color_material="Negro", category="Escombro", stock=2500, storage_pack=50),
    dict(kind="ROLL", sku="ROL-NEG-30-70-100", name="Rollo bolsa tubo negro 30 cm 70 mic 100 m", width_cm=30, length_cm=None, microns=70, meters_per_roll=100, color_material="Negro", category="Bolsa tubo", stock=5, storage_pack=1),
    dict(kind="ROLL", sku="ROL-NEG-35-50-100", name="Rollo bolsa tubo negro 35 cm 50 mic 100 m", width_cm=35, length_cm=None, microns=50, meters_per_roll=100, color_material="Negro", category="Bolsa tubo", stock=5, storage_pack=1),
    dict(kind="BAG", sku="CON-AZU-6090-35", name="Bolsa consorcio azul 60x90 35 mic", width_cm=60, length_cm=90, microns=35, meters_per_roll=None, color_material="Azul", category="Consorcio", stock=2500, storage_pack=50),
    dict(kind="BAG", sku="CRI-4060-60", name="Bolsa polietileno cristal 40x60 60 mic", width_cm=40, length_cm=60, microns=60, meters_per_roll=None, color_material="Transparente", category="Cristal", stock=1000, storage_pack=50),
    dict(kind="ROLL", sku="ROL-TRA-40-70-100", name="Rollo bolsa tubo transparente 40 cm 70 mic 100 m", width_cm=40, length_cm=None, microns=70, meters_per_roll=100, color_material="Transparente", category="Bolsa tubo", stock=3, storage_pack=1),
    dict(kind="ROLL", sku="ROL-TRA-30-50-400", name="Rollo bolsa tubo transparente 30 cm 50 mic 400 m", width_cm=30, length_cm=None, microns=50, meters_per_roll=400, color_material="Transparente", category="Bolsa tubo", stock=1, storage_pack=1),
    dict(kind="ROLL", sku="ROL-TRA-30-70-200", name="Rollo bolsa tubo transparente 30 cm 70 mic 200 m", width_cm=30, length_cm=None, microns=70, meters_per_roll=200, color_material="Transparente", category="Bolsa tubo", stock=4, storage_pack=1),
    dict(kind="BAG", sku="CRI-5070-30", name="Bolsa polietileno cristal 50x70 30 mic", width_cm=50, length_cm=70, microns=30, meters_per_roll=None, color_material="Transparente", category="Cristal", stock=900, storage_pack=50),
    dict(kind="BAG", sku="ESC-NEG-4060-80", name="Bolsa para escombro negra 40x60 80 mic", width_cm=40, length_cm=60, microns=80, meters_per_roll=None, color_material="Negro", category="Escombro", stock=3000, storage_pack=50),
    dict(kind="BAG", sku="CRI-5080-30", name="Bolsa polietileno cristal 50x80 30 mic", width_cm=50, length_cm=80, microns=30, meters_per_roll=None, color_material="Transparente", category="Cristal", stock=800, storage_pack=50),
]

ASSOCIATIONS = [
    (2,"MLA3371021294","LENA-4070-60",100,"Bolsa Para Leña 40x70 Súper Reforzadas X100 Premium"),
    (3,"MLA1806558441","LENA-4070-60",50,"Bolsa Para Leña Para 10kg 40x70 Súper Reforzadas X50 Premium"),
    (4,"MLA3250165026","CON-AMA-6090-35",50,"Bolsa Residuo Peligrosos Amarilla 60x90 X50 Premium Consorc."),
    (5,"MLA1798252383","CON-AZU-80110-35",100,"Bolsas De Consorcio Azul 80x110 Cm Reforzadas X100 Premium"),
    (6,"MLA1742399111","CON-ROJ-6090-35",200,"Bolsas De Consorcio Rojas 60x90 Reforzadas X200 Premium"),
    (7,"MLA1753282189","CON-ROJ-5070-35",50,"Bolsa Roja Patológica 50 X 70 Cm I 45 Micrones X50 Premium"),
    (8,"MLA1920025011","LENA-4070-60",100,"Bolsas 8plast Para Leña 40x70 Ecológicas X100 Unid. Premium"),
    (9,"MLA1960652793","ESC-NEG-5070-100",100,"Bolsas Negras Para Obra 50x70 Extra Gruesas Premium X100 Negro"),
    (11,"MLA2896751378","ROL-NEG-30-70-100",1,"Rollo Negro Polietileno Tubo Bolsa 30cm 70mic 100mts Negro"),
    (12,"MLA3745655034","ESC-NEG-5070-100",50,"Bolsas Para Escombros 50x70 100 Micrones Premium X50 Negro"),
    (13,"MLA3223596848","CON-ROJ-6090-35",100,"Bolsas De Consorcio Rojas 60x90 Reforzadas X100 Premium"),
    (14,"MLA3537506802","CARB-4070-60",100,"Bolsa Para Carbón 10kg 40x70 Súper Reforzadas X100 Premium"),
    (15,"MLA2896659726","ROL-NEG-35-50-100",1,"Rollo Negro Polietileno Tubo Bolsa 35cm 50mic 100mts Negro"),
    (16,"MLA1803582369","CON-AZU-6090-35",100,"Bolsas De Consorcio Azul 60x90 Cm Reforzadas X100 Premium"),
    (19,"MLA3326800390","CRI-4060-60",100,"Bolsas De Polietileno 40 Cm X 60 Cm 60 Micrones X 100 Und Transparente"),
    (20,"MLA1798212667","CON-AZU-80110-35",50,"Bolsas De Consorcio Azul 80x110 Cm Reforzadas X50 Premium"),
    (21,"MLA2890945860","ROL-TRA-40-70-100",1,"Rollo Bolsa Tubo Polietileno 40cm X 100m 70 Micrones Virgen Transparente"),
    (25,"MLA3250167292","CON-AMA-6090-35",100,"Bolsa Residuo Peligrosos Amarilla 60x90 X100 Premium Consorc"),
    (26,"MLA2881081734","ROL-TRA-30-50-400",1,"Rollo Bolsa Tubo Polietileno 30cm X 400m 50 Micrones Virgen Transparente"),
    (27,"MLA3537507044","CARB-4070-60",50,"Bolsa Para Carbón 10kg 40x70 Súper Reforzadas X50 Premium"),
    (28,"MLA3664717340","LENA-4070-60",500,"Bolsa Para Leña 40x70 Súper Reforzadas X500 Premium"),
    (29,"MLA3320383620","ROL-TRA-30-70-200",1,"Rollo Bolsa Tubo Polietileno 30cm X 200m 70 Micrones Virgen Transparente"),
    (30,"MLA3342742490","CRI-4060-60",50,"Bolsas De Polietileno 40 Cm X 60 Cm 60 Micrones X 50 Und Transparente"),
    (34,"MLA3775653192","CRI-5070-30",100,"Bolsas De Polietileno 50x70 Cm 30 Micrones Pack X100 Und Transparente 8plast"),
    (35,"MLA3716706616","ESC-NEG-4060-80",50,"Bolsa Para Escombro Arena Super Reforzada 40x60 Premium X50 Negro"),
    (36,"MLA3745654610","ESC-NEG-5070-100",100,"Bolsas Para Escombros 50x70 100 Micrones Premium X100 Negro"),
    (38,"MLA1960666321","ESC-NEG-5070-100",50,"Bolsas Negras Para Obra 50x70 Extra Gruesas Premium X50 Negro"),
    (39,"MLA1775526815","CON-AMA-90120-100",100,"Bolsas Residuos Especiales Amarillas 100 Mic X100 Premium"),
    (41,"MLA1960291347","CON-ROJ-90120-35",300,"Bolsas Rojas Patológicas Reforzadas 90x120 X300 Premium"),
    (43,"MLA3746050350","ESC-NEG-4060-80",50,"Bolsas Negras Para Obra 40x60 Extra Gruesas Premium X50 Negro"),
    (44,"MLA1981336485","CRI-5080-30",100,"Bolsas De Polietileno 50x80 Cm 30 Micrones Pack X100 Und Transparente 8plast"),
    (53,"MLA1798226971","CON-AZU-80110-35",100,"Bolsas Consorcio Azul 80x110 Reforzadas X100 Premium"),
    (54,"MLA3746087524","ESC-NEG-4060-80",100,"Bolsas Negras Para Obra 40x60 Extra Gruesas Premium X100 Negro"),
    (55,"MLA3716706406","ESC-NEG-4060-80",100,"Bolsa Para Escombro Arena Super Reforzada 40x60 Premium X100 Negro"),
    (61,"MLA3744735098","CON-ROJ-90120-35",200,"Bolsas Rojas Patológicas Reforzadas 90x120 X200 Premium"),
]

EXCLUSIONS = [
    (10,"MLA3096038444","Rollo 20 Cm Bolsa Tubo Polietileno X 120m 50 Micrones Virgen Transparente","NO AGREGAR"),
    (17,"MLA1706707981","Bolsa Tubo Polietileno 15 Cm. De Ancho Cristal X 200 Mt. Transparente","NO AGREGAR"),
    (18,"MLA3158339470","Rollo 70 Cm Con Fuelle Polietileno 40 Mic Bobina 100 Metros Transparente","NO AGREGAR"),
    (22,"MLA1829368267","Rollo Negro Polietileno Tubo Bolsa 20cm 100mic 200mts Negro","NO INCLUIR POR AHORA"),
    (23,"MLA3040488624","Rollo Bolsa Tubo Polietileno 35cm X 100m 70 Micrones Virgen Transparente","NO INCLUIR POR AHORA"),
    (24,"MLA1761653855","Rollo 10 Cm Bolsa Tubo Polietileno X 120m 50 Micrones Virgen Transparente","NO INCLUIR POR AHORA"),
    (31,"MLA2900632568","Rollo Bolsa Tubo Polietileno 25 Cm X 50 Micrones 400m Virgen Transparente","NO AGREGAR"),
    (32,"MLA1722683977","Bolsa Termosellable Rollo 100 Mic. De 25 Ancho X 100 Mt. Transparente","NO AGREGAR"),
    (33,"MLA3037841756","Rollo 50 Cm Bolsa Tubo Polietileno X 100m 50 Micrones Virgen Transparente","NO AGREGAR"),
    (37,"MLA1697381157","Bolsa Tubo 35 Cm Polietileno Termosellable 50 Mic X 500 M Transparente","NO AGREGAR"),
    (40,"MLA2900816838","Rollo Negro Polietileno Tubo Bolsa 30cm 70mic 400mts Negro","NO AGREGAR"),
    (42,"MLA2900618818","Rollo 30 Cm Bolsa Tubo Polietileno 50 Mic Bobina 200 Metros Transparente","NO AGREGAR"),
    (45,"MLA3108687772","Rollo Bolsa Tubo Polietileno 20cm X 100m 100 Micrones Virgen Transparente","NO AGREGAR"),
    (46,"MLA1681506709","Rollo Negro Polietileno Tubo Bolsa 35cm 50mic 400mts Negro","NO AGREGAR"),
    (47,"MLA1699948831","Bolsas Tubo Polietileno Ancho 60 Cm 20 Mic Baja Bobina 500 M Transparente","NO AGREGAR"),
    (48,"MLA1711630987","Rollo Bolsa Termosellable Poliet. 5 Cm. De Ancho X 100 Mt. Transparente","NO AGREGAR"),
    (49,"MLA3059664418","Rollo Bolsa Termosellable De 70 Mic. 10 Cm. Ancho X 120 Mt. Transparente","NO AGREGAR"),
    (50,"MLA1711608519","Rollo Bolsa Termosellable Poliet. 5 Cm. De Ancho X 200 Mt. Transparente","NO AGREGAR"),
    (51,"MLA1686382819","Rollo 90 Cm Bolsa Tubo Polietileno 50 Mic Bobina 100 Metros Transparente","NO AGREGAR"),
    (52,"MLA1722685843","Bolsa Tubo Polietileno 100 Mic. De 20 Ancho X 480 Mt. Transp Transparente","NO AGREGAR"),
    (56,"MLA1681545317","Rollo Negro Polietileno Tubo Bolsa 35cm 50mic 200mts Negro","NO AGREGAR"),
    (57,"MLA2900451828","Rollo Negro Polietileno Tubo Bolsa 30cm 70mic 200mts Negro","NO AGREGAR"),
    (58,"MLA1699946739","Bolsa Tubo 50 Cm Polietileno Termosellable 100 Mic X 100 M Transparente","NO AGREGAR"),
    (59,"MLA1711610329","Rollo Bolsa Termosellable Poliet. 6 Cm. De Ancho X 200 Mt. Transparente","NO AGREGAR"),
    (60,"MLA3108717060","Bolsa Termosellable Rollo 100 Mic. De 20 Ancho X 200 Mt. Transparente","NO AGREGAR"),
]


def main():
    shutil.copy2(DB, BACKUP)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    result = {"backup": str(BACKUP), "products_created": [], "products_reused": [],
              "associations_created": 0, "associations_updated": 0, "associations_unchanged": 0,
              "associations_removed_for_exclusion": 0, "exclusions_created": 0, "exclusions_updated": 0}
    try:
        con.execute("BEGIN IMMEDIATE")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS marketplace_listing_exclusions (
                id INTEGER PRIMARY KEY,
                marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                listing_id TEXT NOT NULL,
                variation_id TEXT NOT NULL DEFAULT '',
                listing_name TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                excluded_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                UNIQUE(marketplace, listing_id, variation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_marketplace_exclusions_active
                ON marketplace_listing_exclusions(marketplace, active, listing_id);
        """)
        for p in NEW_PRODUCTS:
            existing = con.execute("SELECT * FROM products WHERE sku=? COLLATE NOCASE", (p["sku"],)).fetchone()
            if existing:
                fields = (existing["kind"], existing["width_cm"], existing["length_cm"], existing["microns"], existing["meters_per_roll"], existing["color_material"], existing["category"])
                wanted = (p["kind"], p["width_cm"], p["length_cm"], p["microns"], p["meters_per_roll"], p["color_material"], p["category"])
                if fields != wanted or existing["stock"] != p["stock"]:
                    raise RuntimeError(f"El SKU {p['sku']} ya existe con datos o stock distintos")
                result["products_reused"].append(p["sku"])
                continue
            cur = con.execute("""INSERT INTO products
                (kind,sku,name,width_cm,length_cm,microns,meters_per_roll,color_material,category,
                 stock,minimum_stock,target_stock,storage_pack,active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,0,0,0,?,1,?)""",
                (p["kind"],p["sku"],p["name"],p["width_cm"],p["length_cm"],p["microns"],p["meters_per_roll"],
                 p["color_material"],p["category"],p["storage_pack"],now_text()))
            pid = cur.lastrowid
            if p["stock"]:
                con.execute("UPDATE internal_control SET allow_stock_change=1 WHERE id=1")
                con.execute("UPDATE products SET stock=? WHERE id=?", (p["stock"],pid))
                con.execute("""INSERT INTO stock_movements
                    (created_at,product_id,movement_type,quantity_delta,stock_before,stock_after,reason,source,external_key)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (now_text(),pid,"Entrada manual",p["stock"],0,p["stock"],"Configuración manual aprobada · stock inicial","LOCAL",f"MANUAL-CONFIG:{p['sku']}"))
                con.execute("UPDATE internal_control SET allow_stock_change=0 WHERE id=1")
            result["products_created"].append({"sku":p["sku"],"name":p["name"],"stock":p["stock"]})

        for row, listing_id, sku, units, title in ASSOCIATIONS:
            product = con.execute("SELECT id,kind FROM products WHERE sku=? COLLATE NOCASE",(sku,)).fetchone()
            if not product:
                raise RuntimeError(f"No existe producto {sku} para fila {row}")
            if con.execute("SELECT 1 FROM marketplace_listing_exclusions WHERE marketplace='MERCADOLIBRE' AND listing_id=? AND variation_id='' AND active=1",(listing_id,)).fetchone():
                raise RuntimeError(f"La publicación {listing_id} está excluida y también se intentó asociar")
            current = con.execute("SELECT * FROM marketplace_listings WHERE marketplace='MERCADOLIBRE' AND listing_id=? AND variation_id=''",(listing_id,)).fetchone()
            presentation = f"PACK X{units}" if product["kind"] == "BAG" else "ROLLO X1"
            values = (product["id"],units,presentation,title,1,"SOLO_DESCONTAR_VENTAS")
            if current:
                old = (current["product_id"],current["units_consumed"],current["presentation_type"],current["listing_name"],current["active"],current["sync_mode"])
                if old == values:
                    result["associations_unchanged"] += 1
                else:
                    con.execute("""UPDATE marketplace_listings SET product_id=?,units_consumed=?,presentation_type=?,
                        listing_name=?,active=?,sync_mode=?,sync_from=? WHERE id=?""", values+(now_text(),current["id"]))
                    result["associations_updated"] += 1
            else:
                con.execute("""INSERT INTO marketplace_listings
                    (marketplace,listing_id,variation_id,product_id,units_consumed,presentation_type,listing_name,
                     active,sync_mode,last_published_quantity,last_stock_sent,last_synced_at,sync_from)
                    VALUES ('MERCADOLIBRE',?,'',?,?,?,?,1,'SOLO_DESCONTAR_VENTAS',NULL,NULL,NULL,?)""",
                    (listing_id,product["id"],units,presentation,title,now_text()))
                result["associations_created"] += 1

        for row, listing_id, title, reason in EXCLUSIONS:
            removed = con.execute("DELETE FROM marketplace_listings WHERE marketplace='MERCADOLIBRE' AND listing_id=?",(listing_id,)).rowcount
            result["associations_removed_for_exclusion"] += removed
            current = con.execute("SELECT id FROM marketplace_listing_exclusions WHERE marketplace='MERCADOLIBRE' AND listing_id=? AND variation_id=''",(listing_id,)).fetchone()
            if current:
                con.execute("UPDATE marketplace_listing_exclusions SET listing_name=?,reason=?,excluded_at=?,active=1 WHERE id=?",(title,reason,now_text(),current["id"]))
                result["exclusions_updated"] += 1
            else:
                con.execute("""INSERT INTO marketplace_listing_exclusions
                    (marketplace,listing_id,variation_id,listing_name,reason,excluded_at,active)
                    VALUES ('MERCADOLIBRE',?,'',?,?,?,1)""",(listing_id,title,reason,now_text()))
                result["exclusions_created"] += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
