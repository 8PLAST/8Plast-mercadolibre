import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webhook_server.app import create_app


def sample(resource="/orders/123", attempts=1):
    return {"topic":"orders_v2","resource":resource,"user_id":77,"application_id":88,
            "attempts":attempts,"sent":"2026-08-25T20:00:00Z","received":"2026-08-25T20:00:01Z"}


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); self.path=Path(self.folder.name)/"events.db"
        self.env=patch.dict(os.environ,{"WEBHOOK_DATABASE_PATH":str(self.path)}); self.env.start()
        self.client=create_app({"TESTING":True}).test_client()

    def tearDown(self): self.env.stop(); self.folder.cleanup()

    def rows(self,table):
        con=sqlite3.connect(self.path)
        try:return con.execute(f"SELECT * FROM {table}").fetchall()
        finally:con.close()

    def test_health(self):
        response=self.client.get("/health"); self.assertEqual(response.status_code,200); self.assertEqual(response.json,{"status":"ok"})

    def test_valid_post_returns_200_and_persists(self):
        response=self.client.post("/webhook/mercadolibre",json=sample())
        self.assertEqual(response.status_code,200); self.assertFalse(response.json["duplicate"])
        self.assertEqual(len(self.rows("mercadolibre_webhook_events")),1)

    def test_deduplication_keeps_retry_delivery(self):
        self.client.post("/webhook/mercadolibre",json=sample( attempts=1))
        response=self.client.post("/webhook/mercadolibre",json=sample(attempts=2))
        self.assertTrue(response.json["duplicate"])
        self.assertEqual(len(self.rows("mercadolibre_webhook_events")),1)
        self.assertEqual(len(self.rows("mercadolibre_webhook_deliveries")),2)

    def test_incomplete_payload_is_accepted_and_stored(self):
        response=self.client.post("/webhook/mercadolibre",json={"topic":"orders_v2"})
        self.assertEqual(response.status_code,200); self.assertFalse(response.json["complete"])
        self.assertEqual(len(self.rows("mercadolibre_webhook_events")),1)

    def test_invalid_json_is_accepted_and_stored(self):
        response=self.client.post("/webhook/mercadolibre",data="not-json",content_type="text/plain")
        self.assertEqual(response.status_code,200); self.assertEqual(len(self.rows("mercadolibre_webhook_events")),1)

    def test_multiple_notifications(self):
        for order in range(5):self.client.post("/webhook/mercadolibre",json=sample(f"/orders/{order}"))
        self.assertEqual(len(self.rows("mercadolibre_webhook_events")),5)


if __name__ == "__main__": unittest.main()
