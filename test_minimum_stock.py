from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db import Database, StockError


class MinimumStockTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix="8plast_minimum_")
        self.db=Database(Path(self.folder.name)/"test.db")
        self.pid=self.db.add_product({"kind":"BAG","sku":"MIN-TEST","name":"Prueba mínimo",
            "width_cm":40,"length_cm":60,"microns":30,"color_material":"TEST"},100)

    def tearDown(self): self.folder.cleanup()

    def set_stock(self,value):
        current=self.db.product(self.pid)["stock"]
        if current != value: self.db.adjust_stock(self.pid,value,"Prueba de límite")

    def test_zero_is_unconfigured_and_excluded_from_three_counts(self):
        product=self.db.product(self.pid)
        self.assertEqual(self.db.stock_status(product),"SIN CONFIGURAR")
        counts=self.db.stock_status_counts("BAG")
        self.assertEqual((counts["ok"],counts["low"],counts["replenish"],counts["unconfigured"]),(0,0,0,1))

    def test_exact_integer_boundaries(self):
        self.db.update_stock_levels(self.pid,100,125)
        expected={100:"REPONER",101:"BAJO",124:"BAJO",125:"OK",126:"OK"}
        for stock,state in expected.items():
            self.set_stock(stock)
            self.assertEqual(self.db.stock_status(self.db.product(self.pid)),state)

    def test_roll_rounding_boundary(self):
        rid=self.db.add_product({"kind":"ROLL","sku":"ROLL-MIN","name":"Rollo mínimo",
            "width_cm":30,"microns":50,"meters_per_roll":100,"color_material":"TEST"},7)
        self.db.update_stock_levels(rid,5,7)
        self.assertEqual(self.db.stock_status(self.db.product(rid)),"OK")
        self.db.adjust_stock(rid,6,"Prueba")
        self.assertEqual(self.db.stock_status(self.db.product(rid)),"BAJO")
        self.db.adjust_stock(rid,5,"Prueba")
        self.assertEqual(self.db.stock_status(self.db.product(rid)),"REPONER")

    def test_minimum_persists_without_stock_or_movement_change(self):
        before_stock=self.db.product(self.pid)["stock"]
        before_movements=len(self.db.movements())
        self.db.update_stock_levels(self.pid,80,160)
        reopened=Database(self.db.path)
        self.assertEqual((reopened.product(self.pid)["minimum_stock"],reopened.product(self.pid)["target_stock"]),(80,160))
        self.assertEqual(reopened.product(self.pid)["stock"],before_stock)
        self.assertEqual(len(reopened.movements()),before_movements)

    def test_state_filters(self):
        self.db.update_stock_levels(self.pid,100,125)
        self.assertEqual(len(self.db.products("BAG",stock_state="REPONER")),1)
        self.set_stock(110)
        self.assertEqual(len(self.db.products("BAG",stock_state="BAJO")),1)
        self.assertEqual(len(self.db.products("BAG",stock_state="BAJO + REPONER")),1)
        self.set_stock(130)
        self.assertEqual(len(self.db.products("BAG",stock_state="OK")),1)
        self.db.update_stock_levels(self.pid,0,0)
        self.assertEqual(len(self.db.products("BAG",stock_state="SIN CONFIGURAR")),1)

    def test_negative_minimum_is_rejected(self):
        with self.assertRaises(StockError): self.db.update_stock_levels(self.pid,-1,10)


if __name__ == "__main__": unittest.main(verbosity=2)
