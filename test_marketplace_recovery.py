from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import Database, StockError
from ml_integration import MercadoLibreClient, MercadoLibreError


ROOT = Path(__file__).resolve().parent
REAL_DB = ROOT / "8plast_stock.db"
UTC = timezone.utc


def ml_order(number, listing="MLA-RECOVERY-X1", quantity=1, status="paid", at=None, updated=None):
    at = at or datetime(2026, 8, 21, 0, 0, tzinfo=UTC) + timedelta(seconds=number)
    return {"id": f"REC-{number:04d}", "status": status,
            "date_created": at.isoformat(), "date_last_updated": (updated or at).isoformat(),
            "order_items": [{"item": {"id": listing}, "quantity": quantity}]}


class FakeClient(MercadoLibreClient):
    def __init__(self, database, created=None, updated=None, fail=None):
        super().__init__(database)
        self.data = {"date_created": created or [], "date_last_updated": updated or []}
        self.fail = fail
        self.calls = []
        self.write_calls = 0

    def _search_page(self, date_field, start, end, offset, limit=50):
        self.calls.append((date_field, offset, limit, start, end, "GET"))
        if self.fail == (date_field, offset):
            self.fail = None
            raise MercadoLibreError("corte de internet simulado")
        rows = self.data[date_field]
        return {"results": rows[offset:offset + limit],
                "paging": {"total": len(rows), "offset": offset, "limit": limit}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="8plast_recovery_")
        self.path = Path(self.folder.name) / "copy.db"
        source = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
        target = sqlite3.connect(self.path)
        try:
            source.backup(target)
        finally:
            target.close(); source.close()
        self.db = Database(self.path)
        # La copia real puede contener la bandeja activa; cada prueba parte de controles vacíos.
        with self.db.session() as con:
            con.execute("DELETE FROM marketplace_order_inbox")
            con.execute("DELETE FROM marketplace_order_checkpoints")
        self.activation = datetime(2026, 8, 21, tzinfo=UTC)
        self.cutoff = datetime(2026, 8, 22, tzinfo=UTC)
        self.db.configure_marketplace_processing(False, 300, self.activation.isoformat())
        self.product = self.db.add_product({"kind":"BAG","sku":"TEST-RECOVERY","name":"Test recovery",
            "width_cm":10,"length_cm":20,"microns":20,"color_material":"TEST"}, 50000)
        self.db.add_marketplace_listing(self.product,"MLA-RECOVERY-X1",1,presentation_type="x1")
        self.db.add_marketplace_listing(self.product,"MLA-RECOVERY-X50",50,presentation_type="x50")
        self.db.add_marketplace_listing(self.product,"MLA-RECOVERY-X100",100,presentation_type="x100")
        self.db.exclude_marketplace_listing("MLA-EXCLUDED")
        with self.db.session() as con:
            con.execute("UPDATE marketplace_listings SET sync_from=? WHERE product_id=?",
                        (self.activation.isoformat(), self.product))

    def tearDown(self):
        self.folder.cleanup()

    def stock(self):
        return self.db.product(self.product)["stock"]

    def test_more_than_50_pages_chronological_and_no_writes(self):
        orders = [ml_order(i) for i in range(125)]
        client = FakeClient(self.db, orders, orders)
        result = client.sync_orders_recovery(self.cutoff)
        self.assertEqual(result["processing"]["processed"], 125)
        self.assertEqual(self.stock(), 50000 - 125)
        created_offsets = [c[1] for c in client.calls if c[0] == "date_created"]
        self.assertEqual(created_offsets, [0, 50, 100])
        self.assertTrue(all(c[5] == "GET" for c in client.calls))
        self.assertEqual(client.write_calls, 0)
        with self.db.session() as con:
            reasons = [r[0] for r in con.execute("""SELECT reason FROM stock_movements
                WHERE external_key LIKE 'ML:SALE:REC-%' ORDER BY id""")]
        self.assertIn("REC-0000", reasons[0]); self.assertIn("REC-0124", reasons[-1])

    def test_network_failure_does_not_advance_and_retry_recovers_once(self):
        orders = [ml_order(i) for i in range(80)]
        client = FakeClient(self.db, orders, [], fail=("date_created", 50))
        with self.assertRaises(MercadoLibreError):
            client.sync_orders_recovery(self.cutoff)
        checkpoint = self.db.marketplace_checkpoint("CREATED")
        self.assertEqual(checkpoint["last_run_status"], "ERROR")
        self.assertIsNone(checkpoint["covered_through"])
        self.assertEqual(self.stock(), 50000)
        result = client.sync_orders_recovery(self.cutoff)
        self.assertEqual(result["processing"]["processed"], 80)
        self.assertEqual(self.stock(), 49920)

    def test_crash_after_stock_write_restarts_as_duplicate(self):
        order = ml_order(1)
        self.db.store_marketplace_orders([order])
        direct = self.db.process_marketplace_order(order)
        after_direct = self.stock()
        self.assertEqual(direct["processed"], 1)
        resumed = self.db.process_marketplace_inbox()
        self.assertEqual(resumed["duplicates"], 1)
        self.assertEqual(self.stock(), after_direct)

    def test_packs_shared_stock_excluded_unassociated_and_old(self):
        start = self.stock()
        rows = [ml_order(1,"MLA-RECOVERY-X50"), ml_order(2,"MLA-RECOVERY-X100"),
                ml_order(3,"MLA-EXCLUDED"), ml_order(4,"MLA-NOT-LINKED"),
                ml_order(5,"MLA-RECOVERY-X1",at=self.activation-timedelta(seconds=1))]
        self.db.store_marketplace_orders(rows)
        result = self.db.process_marketplace_inbox()
        self.assertEqual(start-self.stock(), 150)
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["unassociated"], 1)
        self.assertEqual(self.db.marketplace_listing("MLA-RECOVERY-X50")["product_id"],
                         self.db.marketplace_listing("MLA-RECOVERY-X100")["product_id"])

    def test_cancellation_and_repeated_update_never_restock(self):
        sale = ml_order(8,"MLA-RECOVERY-X50")
        self.db.store_marketplace_orders([sale]); self.db.process_marketplace_inbox()
        sold_stock = self.stock()
        cancel = ml_order(8,"MLA-RECOVERY-X50",status="cancelled",
                          updated=datetime(2026,8,21,1,tzinfo=UTC))
        self.db.store_marketplace_orders([cancel]); first = self.db.process_marketplace_inbox()
        repeat = dict(cancel); repeat["date_last_updated"] = datetime(2026,8,21,2,tzinfo=UTC).isoformat()
        self.db.store_marketplace_orders([repeat]); second = self.db.process_marketplace_inbox()
        self.assertEqual(first["cancellations_pending"], 1)
        self.assertEqual(second["cancellation_repeats"], 1)
        self.assertEqual(self.stock(), sold_stock)
        reviews = [r for r in self.db.marketplace_order_reviews() if r["order_id"] == "REC-0008"]
        self.assertEqual(len(reviews), 1); self.assertEqual(reviews[0]["seen_count"], 2)

    def test_ml_sales_allow_negative_balance_once_and_production_reduces_debt(self):
        with self.db.session() as con:
            con.execute("UPDATE internal_control SET allow_stock_change=1 WHERE id=1")
            con.execute("UPDATE products SET stock=10 WHERE id=?", (self.product,))
            con.execute("UPDATE internal_control SET allow_stock_change=0 WHERE id=1")
        self.db.store_marketplace_orders([ml_order(1,"MLA-RECOVERY-X50"),ml_order(2)])
        result = self.db.process_marketplace_inbox()
        self.assertEqual(len(result["errors"]), 0)
        with self.db.session() as con:
            statuses = [r[0] for r in con.execute("""SELECT processing_status FROM marketplace_order_inbox
                ORDER BY date_created,order_id""")]
        self.assertEqual(statuses, ["PROCESSED","PROCESSED"])
        self.assertEqual(self.stock(), -41)
        self.db.retry_marketplace_inbox_errors()
        self.db.process_marketplace_inbox()
        self.assertEqual(self.stock(), -41)
        self.assertEqual(self.db.process_marketplace_order(ml_order(1,'MLA-RECOVERY-X50'))['duplicates'], 1)
        self.assertEqual(self.stock(), -41)
        self.db.add_production(self.product, 10)
        self.assertEqual(self.stock(), -31)
        with self.assertRaises(StockError):
            self.db.move_stock(self.product, 1, 'Salida manual', 'No permitido')

    def test_single_worker_file_lock(self):
        from runtime_config import file_lock
        lock_path = Path(self.folder.name) / 'worker.lock'
        with file_lock(lock_path):
            with self.assertRaises(TimeoutError):
                with file_lock(lock_path): pass

    def test_empty_page_before_total_does_not_advance_checkpoint(self):
        client = FakeClient(self.db)
        client._search_page = lambda *args: {'results': [], 'paging': {'total': 1}}
        with self.assertRaises(MercadoLibreError):
            client.sync_orders_recovery(self.cutoff)
        self.assertIsNone(self.db.marketplace_checkpoint('CREATED')['covered_through'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
