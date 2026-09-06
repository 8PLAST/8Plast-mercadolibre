import tempfile
import unittest
import sqlite3
from pathlib import Path

from db import Database, StockError


def ml_order(order_id, listing_id, quantity=1, status="paid", date_created="2999-01-01T00:00:00Z"):
    return {"id":order_id,"status":status,"date_created":date_created,
            "order_items":[{"item":{"id":listing_id,"variation_id":None},"quantity":quantity}]}


class StockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Database(Path(self.tmp.name)/"test.db")
        self.pid=self.db.add_product({"kind":"BAG","sku":"B-1","name":"Bolsa prueba","width_cm":50,
            "length_cm":70,"microns":30,"meters_per_roll":None,"minimum_stock":500,
            "target_stock":2000},2000)

    def tearDown(self): self.tmp.cleanup()

    def test_master_stock_and_presentations(self):
        self.db.move_stock(self.pid,200,"Venta","Prueba")
        p=self.db.product(self.pid)
        self.assertEqual(p["stock"],1800)
        self.assertEqual([p["stock"]//x for x in self.db.presentations()],[36,18,9,6,3])

    def test_no_negative_stock(self):
        with self.assertRaises(StockError): self.db.move_stock(self.pid,2001,"Venta","")
        self.assertEqual(self.db.product(self.pid)["stock"],2000)

    def test_every_change_has_movement(self):
        self.assertEqual(len(self.db.movements()),1)
        self.db.move_stock(self.pid,50,"Entrada de producción","")
        self.assertEqual(len(self.db.movements()),2)

    def test_minimum_and_target_production_amount(self):
        self.db.adjust_stock(self.pid,350,"Conteo")
        product=self.db.product(self.pid)
        self.assertLessEqual(product["stock"],product["minimum_stock"])
        self.assertEqual(self.db.production_needed(product),1650)
        self.assertEqual((self.db.production_needed(product)+49)//50,33)

    def test_marketplace_quantity_is_separate_from_physical_stock(self):
        self.db.add_marketplace_listing(self.pid,"MLA-TEST",200,presentation_type="x200",published_quantity=999)
        listing_before=self.db.marketplace_listing("MLA-TEST")
        self.assertEqual(listing_before["sync_mode"],"SOLO_DESCONTAR_VENTAS")
        self.assertEqual(listing_before["last_published_quantity"],999)
        self.db.move_stock(self.pid,200,"Venta","Venta futura MLA",source="MERCADOLIBRE",external_key="ORDER-1")
        self.assertEqual(self.db.product(self.pid)["stock"],1800)
        listing_after=self.db.marketplace_listing("MLA-TEST")
        self.assertEqual(listing_after["last_published_quantity"],999)

    def test_duplicate_marketplace_sale_is_rejected(self):
        self.db.move_stock(self.pid,50,"Venta","ML",source="MERCADOLIBRE",external_key="ORDER-UNIQUE")
        with self.assertRaises(StockError):
            self.db.move_stock(self.pid,50,"Venta","ML repetida",source="MERCADOLIBRE",external_key="ORDER-UNIQUE")
        self.assertEqual(self.db.product(self.pid)["stock"],1950)

    def test_storage_pack_50_and_production(self):
        product=self.db.product(self.pid); self.assertEqual(product["storage_pack"],50)
        self.db.add_production(self.pid,10,as_storage_packs=True)
        self.assertEqual(self.db.product(self.pid)["stock"],2500)

    def test_storage_pack_25_and_sale_x100(self):
        pid=self.db.add_product({"kind":"BAG","sku":"B-25","name":"Bolsa pack 25","width_cm":90,
            "length_cm":120,"microns":100,"minimum_stock":100,"target_stock":1000,"storage_pack":25},1000)
        self.assertEqual(self.db.product(pid)["stock"]//25,40)
        self.db.add_production(pid,10,as_storage_packs=True)
        self.assertEqual(self.db.product(pid)["stock"],1250)
        self.db.add_marketplace_listing(pid,"MLA-X100",100,presentation_type="x100")
        result=self.db.process_marketplace_order(ml_order("O-100","MLA-X100"))
        self.assertEqual(result["processed"],1); self.assertEqual(self.db.product(pid)["stock"],1150)
        self.assertEqual(self.db.product(pid)["stock"]//25,46)

    def test_sale_x200_storage_pack_50_and_multiple_listings(self):
        self.db.add_marketplace_listing(self.pid,"MLA-A",200,presentation_type="x200")
        self.db.add_marketplace_listing(self.pid,"MLA-B",100,presentation_type="x100")
        self.assertEqual(len(self.db.marketplace_listings()),2)
        self.db.process_marketplace_order(ml_order("O-200","MLA-A"))
        self.assertEqual(self.db.product(self.pid)["stock"],1800)

    def test_order_duplicate_and_cancellation(self):
        self.db.add_marketplace_listing(self.pid,"MLA-C",200,presentation_type="x200")
        order=ml_order("ORDER-C","MLA-C")
        self.db.process_marketplace_order(order); self.db.process_marketplace_order(order)
        self.assertEqual(self.db.product(self.pid)["stock"],1800)
        order["status"]="cancelled"
        first=self.db.process_marketplace_order(order); second=self.db.process_marketplace_order(order)
        self.assertEqual(self.db.product(self.pid)["stock"],1800)
        self.assertEqual(first["cancellations_pending"],1)
        self.assertEqual(second["cancellation_repeats"],1)
        reviews=self.db.marketplace_order_reviews()
        self.assertEqual(len(reviews),1); self.assertEqual(reviews[0]["seen_count"],2)

    def test_excluded_listing_does_not_change_stock(self):
        self.db.exclude_marketplace_listing("MLA-EXCLUDED",reason="Prueba")
        result=self.db.process_marketplace_order(ml_order("ORDER-E","MLA-EXCLUDED"))
        self.assertEqual(self.db.product(self.pid)["stock"],2000)
        self.assertEqual(result["excluded"],["MLA-EXCLUDED"])

    def test_automatic_processing_defaults_to_disabled_five_minutes(self):
        config=self.db.marketplace_processing_config()
        self.assertEqual(config["enabled"],0)
        self.assertEqual(config["interval_seconds"],300)

    def test_return_is_idempotent(self):
        self.db.add_marketplace_listing(self.pid,"MLA-R",50,presentation_type="x50")
        self.db.process_marketplace_order(ml_order("ORDER-R","MLA-R",2))
        self.assertTrue(self.db.process_marketplace_return("ORDER-R","MLA-R","",2,"RETURN-1"))
        self.assertFalse(self.db.process_marketplace_return("ORDER-R","MLA-R","",2,"RETURN-1"))
        self.assertEqual(self.db.product(self.pid)["stock"],2000)

    def test_roll_marketplace_pack_x2(self):
        pid=self.db.add_product({"kind":"ROLL","sku":"R-1","name":"Rollo","width_cm":30,"microns":50,
            "meters_per_roll":100,"minimum_stock":2,"target_stock":20},20)
        self.db.add_marketplace_listing(pid,"MLA-ROLL",2,presentation_type="x2")
        self.db.process_marketplace_order(ml_order("ORDER-ROLL","MLA-ROLL"))
        self.assertEqual(self.db.product(pid)["stock"],18)

    def test_association_sets_sync_start_automatically(self):
        association_id=self.db.add_marketplace_listing(self.pid,"MLA-START",100)
        association=self.db.marketplace_listing_by_id(association_id)
        self.assertTrue(association["sync_from"])
        self.assertEqual(association["sync_mode"],"SOLO_DESCONTAR_VENTAS")

    def test_historical_sale_before_association_is_ignored(self):
        self.db.add_marketplace_listing(self.pid,"MLA-HISTORY",100)
        result=self.db.process_marketplace_order(ml_order("OLD-ORDER","MLA-HISTORY",date_created="2020-01-01T00:00:00Z"))
        self.assertEqual(result["processed"],0)
        self.assertEqual(result["skipped_before_start"],1)
        self.assertEqual(self.db.product(self.pid)["stock"],2000)

    def test_sale_after_sync_start_only_processes_once(self):
        self.db.add_marketplace_listing(self.pid,"MLA-NEW",100)
        order=ml_order("NEW-ORDER","MLA-NEW")
        first=self.db.process_marketplace_order(order); second=self.db.process_marketplace_order(order)
        self.assertEqual(first["processed"],1); self.assertEqual(second["duplicates"],1)
        self.assertEqual(self.db.product(self.pid)["stock"],1900)

    def test_sale_without_date_is_safely_ignored(self):
        self.db.add_marketplace_listing(self.pid,"MLA-NODATE",100)
        order=ml_order("NO-DATE","MLA-NODATE"); order.pop("date_created")
        result=self.db.process_marketplace_order(order)
        self.assertEqual(result["skipped_missing_date"],1)
        self.assertEqual(self.db.product(self.pid)["stock"],2000)


class MigrationTests(unittest.TestCase):
    def test_existing_product_and_movement_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"legacy.db"; con=sqlite3.connect(path)
            con.executescript("""CREATE TABLE products(id INTEGER PRIMARY KEY,kind TEXT,sku TEXT UNIQUE,name TEXT,width_cm REAL,
                length_cm REAL,microns REAL,meters_per_roll REAL,color_material TEXT,category TEXT,stock INTEGER,
                minimum_stock INTEGER,target_stock INTEGER,active INTEGER,created_at TEXT);
                INSERT INTO products VALUES(1,'BAG','LEGACY','Existente',50,70,30,NULL,'','',1750,500,2000,1,'2026');
                CREATE TABLE stock_movements(id INTEGER PRIMARY KEY,created_at TEXT,product_id INTEGER,movement_type TEXT,
                quantity_delta INTEGER,stock_before INTEGER,stock_after INTEGER,reason TEXT,source TEXT,external_key TEXT UNIQUE);
                INSERT INTO stock_movements VALUES(1,'2026',1,'Entrada manual',1750,0,1750,'Inicial','LOCAL',NULL);
                CREATE TABLE marketplace_listings(id INTEGER PRIMARY KEY,marketplace TEXT,listing_id TEXT,
                variation_id TEXT,product_id INTEGER,units_consumed INTEGER,presentation_type TEXT,listing_name TEXT,
                active INTEGER,sync_mode TEXT,last_published_quantity INTEGER,last_stock_sent INTEGER,last_synced_at TEXT,
                UNIQUE(marketplace,listing_id,variation_id));
                INSERT INTO marketplace_listings VALUES(1,'MERCADOLIBRE','MLA-LEGACY','',1,100,'x100','Existente',1,
                'SOLO_DESCONTAR_VENTAS',NULL,NULL,NULL);"""); con.commit(); con.close()
            db=Database(path)
            self.assertEqual(db.product(1)["stock"],1750); self.assertEqual(db.product(1)["storage_pack"],50)
            self.assertEqual(len(db.movements()),1)
            self.assertTrue(db.marketplace_listing_by_id(1)["sync_from"])


if __name__ == "__main__": unittest.main()
