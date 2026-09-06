import tempfile
import unittest
from pathlib import Path

from app import ROLL_MICRON_TABS, filter_rolls_by_microns
from db import Database


class RollMicronTabTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.folder.name)/"roll_colors.db")
        self.ids={}
        for microns in (50,70,80):
            self.ids[microns]=self.db.add_product({"kind":"ROLL","sku":f"R-{microns}",
                "name":f"Rollo {microns}","width_cm":30,"microns":microns,
                "meters_per_roll":100,"color_material":"Transparente"},10)
        self.bag=self.db.add_product({"kind":"BAG","sku":"B-1","name":"Bolsa","width_cm":40,
            "length_cm":60,"microns":50,"color_material":"Negro"},100)

    def tearDown(self): self.folder.cleanup()

    def test_tabs_filter_existing_rolls_without_duplicates(self):
        rolls=list(self.db.products("ROLL",active="Activos"))
        self.assertEqual(ROLL_MICRON_TABS,("Todos","50 micrones","70 micrones"))
        self.assertEqual({p["id"] for p in filter_rolls_by_microns(rolls,"Todos")},set(self.ids.values()))
        self.assertEqual([p["id"] for p in filter_rolls_by_microns(rolls,"50 micrones")],[self.ids[50]])
        self.assertEqual([p["id"] for p in filter_rolls_by_microns(rolls,"70 micrones")],[self.ids[70]])
        self.assertIn(self.ids[80],[p["id"] for p in filter_rolls_by_microns(rolls,"Todos")])

    def test_filtered_roll_keeps_stock_edit_detail_and_listing_actions(self):
        roll=filter_rolls_by_microns(self.db.products("ROLL"),"50 micrones")[0]
        product_id=roll["id"]
        listing=self.db.add_marketplace_listing(product_id,"MLA-ROLL-50",1,presentation_type="x1")
        self.db.move_stock(product_id,5,"Entrada manual","Entrada desde pestaña")
        self.db.move_stock(product_id,2,"Venta","Salida desde pestaña")
        data=dict(self.db.product(product_id)); data["name"]="Rollo 50 editado"
        self.db.update_product(product_id,data)
        detail=self.db.product(product_id)
        association=self.db.marketplace_listing_by_id(listing)
        self.assertEqual(detail["stock"],13)
        self.assertEqual(detail["name"],"Rollo 50 editado")
        self.assertEqual(detail["sku"],"R-50")
        self.assertEqual(association["product_id"],product_id)
        self.assertEqual(association["units_consumed"],1)
        self.assertEqual(self.db.product(self.bag)["stock"],100)


if __name__=="__main__": unittest.main()
