import tempfile
import unittest
from pathlib import Path

from app import BAG_COLOR_TABS, filter_bags_by_color
from db import Database


class BagColorTabTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.folder.name)/"colors.db")
        self.ids={}
        for color in ("Negro","Verde","Amarillo","Azul","Rojo","Transparente",""):
            key=color or "SinColor"
            self.ids[key]=self.db.add_product({"kind":"BAG","sku":"C-"+key,"name":"Bolsa "+key,
                "width_cm":40,"length_cm":60,"microns":35,"color_material":color},100)
        self.roll=self.db.add_product({"kind":"ROLL","sku":"R-1","name":"Rollo negro","width_cm":30,
            "microns":50,"meters_per_roll":100,"color_material":"Negro"},5)

    def tearDown(self): self.folder.cleanup()

    def test_tabs_filter_existing_rows_without_duplicates(self):
        bags=list(self.db.products("BAG",active="Activos"))
        self.assertEqual(BAG_COLOR_TABS,("Todas","Negras","Verdes","Amarillas","Azules","Rojas","Polietileno cristal"))
        self.assertEqual(len(filter_bags_by_color(bags,"Todas")),7)
        expected={"Negras":"Negro","Verdes":"Verde","Amarillas":"Amarillo","Azules":"Azul","Rojas":"Rojo"}
        for tab,color in expected.items():
            result=filter_bags_by_color(bags,tab)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]["id"],self.ids[color])
        self.assertNotIn(self.ids["SinColor"],[p["id"] for p in filter_bags_by_color(bags,"Negras")])
        self.assertEqual(len({p["id"] for p in filter_bags_by_color(bags,"Todas")}),7)

    def test_filtered_product_keeps_normal_stock_edit_and_detail_actions(self):
        black=filter_bags_by_color(self.db.products("BAG"),"Negras")[0]
        product_id=black["id"]
        self.db.move_stock(product_id,20,"Entrada manual","Prueba desde pestaña")
        self.db.move_stock(product_id,15,"Venta","Descuento desde pestaña")
        data=dict(self.db.product(product_id)); data["name"]="Bolsa negra editada"
        self.db.update_product(product_id,data)
        detail=self.db.product(product_id)
        self.assertEqual(detail["stock"],105)
        self.assertEqual(detail["name"],"Bolsa negra editada")
        self.assertEqual(detail["sku"],"C-Negro")
        self.assertEqual(self.db.product(self.roll)["stock"],5)


if __name__=="__main__": unittest.main()
