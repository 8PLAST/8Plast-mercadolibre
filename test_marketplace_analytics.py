import json
import sqlite3
import unittest
from datetime import datetime,timezone

from marketplace_analytics import adjusted_velocity,coverage_label,marketplace_metrics,replenishment_priority,trend_for


class MarketplaceAnalyticsTests(unittest.TestCase):
    def test_pack_factor_sales_and_coverage(self):
        con=sqlite3.connect(":memory:"); con.row_factory=sqlite3.Row
        con.executescript("""
          CREATE TABLE products(id INTEGER,sku TEXT,name TEXT,stock INTEGER,minimum_stock INTEGER,target_stock INTEGER,active INTEGER);
          CREATE TABLE marketplace_listings(id INTEGER,marketplace TEXT,listing_id TEXT,variation_id TEXT,product_id INTEGER,units_consumed INTEGER,presentation_type TEXT,listing_name TEXT,active INTEGER,last_published_quantity INTEGER);
          CREATE TABLE marketplace_order_inbox(order_id TEXT,date_created TEXT,payload_json TEXT,marketplace TEXT);
          INSERT INTO products VALUES(1,'BAG-X','Bolsa',1000,500,2000,1);
          INSERT INTO marketplace_listings VALUES(1,'MERCADOLIBRE','MLA-X','',1,100,'x100','Pack',1,5);
        """)
        now=datetime(2026,8,29,tzinfo=timezone.utc)
        order={"status":"paid","order_items":[{"item":{"id":"MLA-X"},"quantity":3}]}
        con.execute("INSERT INTO marketplace_order_inbox VALUES(?,?,?,'MERCADOLIBRE')",('O1','2026-08-28T00:00:00+00:00',json.dumps(order)))
        metrics,unlinked=marketplace_metrics(con,now)
        self.assertEqual(metrics[1]["sales_7"],300)
        self.assertEqual(metrics[1]["sales_30"],300)
        self.assertEqual(metrics[1]["published_stock"],500)
        self.assertAlmostEqual(metrics[1]["velocity"],10)
        expected=adjusted_velocity({7:300,30:300,90:300,180:300})
        self.assertAlmostEqual(metrics[1]["adjusted_velocity"],expected)
        self.assertAlmostEqual(metrics[1]["coverage"],1000/expected)
        self.assertEqual(unlinked,[])

    def test_thresholds_and_priority_are_explicit(self):
        self.assertEqual(coverage_label(6.9),"CRÍTICA")
        self.assertEqual(coverage_label(20),"MEDIA")
        self.assertEqual(coverage_label(None),"SIN DATOS")
        product={"stock":20,"minimum_stock":10,"target_stock":100}
        self.assertEqual(replenishment_priority(product,4,5),"URGENTE")

    def test_adjusted_velocity_and_trend(self):
        growing={7:140,30:300,90:450,180:900}
        self.assertAlmostEqual(adjusted_velocity(growing),.35*20+.35*10+.20*5+.10*5)
        self.assertIn(trend_for(growing)[0],{"EN CRECIMIENTO","FUERTE CRECIMIENTO"})


if __name__=="__main__": unittest.main()
