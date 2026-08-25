import tempfile
import unittest
import sqlite3
from pathlib import Path

from db import Database, StockError


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
        result=self.db.process_marketplace_order({"id":"O-100","status":"paid","order_items":[{"item":{"id":"MLA-X100","variation_id":None},"quantity":1}]})
        self.assertEqual(result["processed"],1); self.assertEqual(self.db.product(pid)["stock"],1150)
        self.assertEqual(self.db.product(pid)["stock"]//25,46)

    def test_sale_x200_storage_pack_50_and_multiple_listings(self):
        self.db.add_marketplace_listing(self.pid,"MLA-A",200,presentation_type="x200")
        self.db.add_marketplace_listing(self.pid,"MLA-B",100,presentation_type="x100")
        self.assertEqual(len(self.db.marketplace_listings()),2)
        self.db.process_marketplace_order({"id":"O-200","status":"paid","order_items":[{"item":{"id":"MLA-A"},"quantity":1}]})
        self.assertEqual(self.db.product(self.pid)["stock"],1800)

    def test_order_duplicate_and_cancellation(self):
        self.db.add_marketplace_listing(self.pid,"MLA-C",200,presentation_type="x200")
        order={"id":"ORDER-C","status":"paid","order_items":[{"item":{"id":"MLA-C"},"quantity":1}]}
        self.db.process_marketplace_order(order); self.db.process_marketplace_order(order)
        self.assertEqual(self.db.product(self.pid)["stock"],1800)
        order["status"]="cancelled"; self.db.process_marketplace_order(order); self.db.process_marketplace_order(order)
        self.assertEqual(self.db.product(self.pid)["stock"],2000)

    def test_return_is_idempotent(self):
        self.db.add_marketplace_listing(self.pid,"MLA-R",50,presentation_type="x50")
        self.db.process_marketplace_order({"id":"ORDER-R","status":"paid","order_items":[{"item":{"id":"MLA-R"},"quantity":2}]})
        self.assertTrue(self.db.process_marketplace_return("ORDER-R","MLA-R","",2,"RETURN-1"))
        self.assertFalse(self.db.process_marketplace_return("ORDER-R","MLA-R","",2,"RETURN-1"))
        self.assertEqual(self.db.product(self.pid)["stock"],2000)

    def test_roll_marketplace_pack_x2(self):
        pid=self.db.add_product({"kind":"ROLL","sku":"R-1","name":"Rollo","width_cm":30,"microns":50,
            "meters_per_roll":100,"minimum_stock":2,"target_stock":20},20)
        self.db.add_marketplace_listing(pid,"MLA-ROLL",2,presentation_type="x2")
        self.db.process_marketplace_order({"id":"ORDER-ROLL","status":"paid","order_items":[{"item":{"id":"MLA-ROLL"},"quantity":1}]})
        self.assertEqual(self.db.product(pid)["stock"],18)


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
                INSERT INTO stock_movements VALUES(1,'2026',1,'Entrada manual',1750,0,1750,'Inicial','LOCAL',NULL);"""); con.commit(); con.close()
            db=Database(path)
            self.assertEqual(db.product(1)["stock"],1750); self.assertEqual(db.product(1)["storage_pack"],50)
            self.assertEqual(len(db.movements()),1)


if __name__ == "__main__": unittest.main()
