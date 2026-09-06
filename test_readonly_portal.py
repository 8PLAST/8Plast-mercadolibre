from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db import Database
from readonly_portal.app import connect_readonly, create_app


ROOT=Path(__file__).resolve().parent
REAL_DB=ROOT/"8plast_stock.db"


def digest(path):
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()


class ReadOnlyPortalTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix="8plast_portal_")
        self.copy=Path(self.folder.name)/"copy.db"
        source=sqlite3.connect(f"file:{REAL_DB}?mode=ro",uri=True); target=sqlite3.connect(self.copy)
        try: source.backup(target)
        finally: target.close(); source.close()
        self.app=create_app({"TESTING":True,"DATABASE_PATH":str(self.copy)})
        self.client=self.app.test_client()

    def tearDown(self): self.folder.cleanup()

    def test_all_pages_and_filters(self):
        for path in ("/","/bolsas","/rollos","/movimientos","/health",
                     "/bolsas?q=negra&state=REPONER","/rollos?state=SIN+CONFIGURAR",
                     "/movimientos?type=Venta"):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,path)
        self.assertIn("CON-NEG-90120-35",self.client.get("/bolsas?q=90120").get_data(as_text=True))
        self.assertIn("solo lectura",self.client.get("/").get_data(as_text=True).lower())

    def test_every_unsafe_http_method_is_rejected(self):
        for method in ("post","put","patch","delete"):
            for path in ("/","/bolsas","/rollos","/movimientos","/health"):
                self.assertEqual(getattr(self.client,method)(path,json={"stock":0}).status_code,405)

    def test_bag_color_tabs_filter_only_existing_products(self):
        all_page=self.client.get("/bolsas?color=Todas").get_data(as_text=True)
        black_page=self.client.get("/bolsas?color=Negras").get_data(as_text=True)
        green_page=self.client.get("/bolsas?color=Verdes").get_data(as_text=True)
        rolls_page=self.client.get("/rollos").get_data(as_text=True)
        self.assertIn("CON-NEG-90120-35",all_page)
        self.assertIn("CON-NEG-90120-35",black_page)
        self.assertNotIn("CON-NEG-90120-35",green_page)
        self.assertIn("CRI-2040-50",all_page)
        self.assertNotIn("CRI-2040-50",black_page)
        self.assertNotIn('aria-label="Filtrar bolsas por color"',rolls_page)

    def test_roll_micron_tabs_filter_only_existing_products(self):
        all_page=self.client.get("/rollos?microns=Todos").get_data(as_text=True)
        fifty=self.client.get("/rollos?microns=50+micrones").get_data(as_text=True)
        seventy=self.client.get("/rollos?microns=70+micrones").get_data(as_text=True)
        bags=self.client.get("/bolsas").get_data(as_text=True)
        self.assertIn("ROL-TRA-30-50-100",all_page)
        self.assertIn("ROL-TRA-30-70-100",all_page)
        self.assertIn("ROL-TRA-15-150-100",all_page)
        self.assertIn("ROL-TRA-30-50-100",fifty)
        self.assertNotIn("ROL-TRA-30-70-100",fifty)
        self.assertIn("ROL-TRA-30-70-100",seventy)
        self.assertNotIn("ROL-TRA-30-50-100",seventy)
        self.assertNotIn('aria-label="Filtrar rollos por micronaje"',bags)

    def test_database_connection_is_enforced_read_only(self):
        con=connect_readonly(self.copy)
        try:
            self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0],1)
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("UPDATE products SET stock=0")
        finally: con.close()

    def test_portal_observes_new_committed_data_without_writing(self):
        db=Database(self.copy); product=db.products()[0]
        before=product["stock"]
        db.move_stock(product["id"],1,"Entrada manual","Prueba de actualización")
        page=self.client.get(f"/bolsas?q={product['sku']}").get_data(as_text=True)
        self.assertIn(str(before+1),page)

    def test_security_headers_and_no_admin_routes(self):
        response=self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"],"DENY")
        self.assertEqual(response.headers["Cache-Control"],"no-store")
        for path in ("/admin","/editar","/api/stock","/mercadolibre/sync"):
            self.assertEqual(self.client.get(path).status_code,404)

    def test_real_database_unchanged_by_all_portal_tests(self):
        before=digest(REAL_DB)
        for path in ("/","/bolsas","/rollos","/movimientos"):
            self.client.get(path)
        self.assertEqual(digest(REAL_DB),before)


if __name__=="__main__": unittest.main(verbosity=2)
