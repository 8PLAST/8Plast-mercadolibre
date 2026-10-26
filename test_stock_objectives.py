import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from db import Database
from readonly_portal.app import create_app
from stock_objectives import LOCAL, recommend, stock_objectives


class ObjectiveTests(unittest.TestCase):
    def product(self, stock=100, target=50):
        return dict(stock=stock, target_stock=target)

    def test_fifteen_days_includes_lead_time_and_manual_target_is_not_floor(self):
        r=recommend(self.product(target=9999),[10]*90,[True]*90,0)
        self.assertEqual(r['target_recommended'],150)
        self.assertEqual(r['reorder_point'],50)
        self.assertEqual(r['target_difference'],-9849)
        self.assertEqual(r['safety_stock'],0)

    def test_reservations_and_lead_time_risk(self):
        r=recommend(self.product(stock=100),[10]*90,[True]*90,60)
        self.assertEqual(r['coverage_days'],4)
        self.assertEqual(r['priority'],'CRÍTICO')
        self.assertEqual(r['risk'],'ALTO')

    def test_stockout_days_do_not_lower_demand(self):
        daily=[10]*90; available=[True]*90
        for i in range(0,90,3): daily[i]=0; available[i]=False
        r=recommend(self.product(),daily,available,0)
        self.assertEqual(r['forecast_daily'],10)
        self.assertEqual(r['target_recommended'],150)

    def test_missing_evidence_is_not_zero(self):
        r=recommend(self.product(),[10]*90,[None]*90)
        self.assertIsNone(r['target_recommended'])
        self.assertIsNone(r['reorder_point'])
        self.assertEqual(r['priority'],'REVISAR DATOS')

    def test_variability_uses_one_margin(self):
        r=recommend(self.product(),[0,20]*45,[True]*90,0)
        self.assertGreater(r['safety_stock'],0)
        self.assertLessEqual(abs((r['target_recommended']-r['reorder_point'])-r['forecast_daily']*10),1)

    def test_verified_sales_allow_estimate_with_warnings(self):
        r=recommend(self.product(),[10]*90,[None]*90,errors=['Auditoría parcial'],observed_only=True)
        self.assertEqual(r['target_recommended'],150)
        self.assertEqual(r['reorder_point'],50)
        self.assertEqual(r['forecast_daily'],10)
        self.assertTrue(r['provisional'])
        self.assertTrue(r['issues'])
        r=recommend(self.product(),[0]*90,[None]*90,observed_only=True)
        self.assertIsNone(r['target_recommended'])

    def test_zero_demand_does_not_create_negative_or_infinite_coverage(self):
        r=recommend(self.product(),[0]*90,[True]*90,0)
        self.assertEqual(r['target_recommended'],0)
        self.assertEqual(r['priority'],'SIN NECESIDAD')
        self.assertIsNone(r['coverage_days'])
        r=recommend(self.product(stock=-1),[10]*90,[True]*90,0)
        self.assertEqual(r['priority'],'REVISAR DATOS')


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        self.db=Database(self.path)
        self.now=datetime(2026,10,26,17,tzinfo=LOCAL)
        self.pids=[]
        for name in ('Roja','Verde'):
            self.pids.append(self.db.add_product(dict(kind='BAG',sku=name,name=name,
                width_cm=50,length_cm=70,microns=35,color_material=name,
                category='Consorcio',minimum_stock=20,target_stock=100),initial_stock=100))
        with self.db.transaction() as c:
            for v,pid,factor in [('red',self.pids[0],50),('green',self.pids[1],100)]:
                c.execute("INSERT INTO marketplace_listings(marketplace,listing_id,variation_id,product_id,units_consumed,sync_from) VALUES('MERCADOLIBRE','MLA1',?,?,?,'2026-01-01')",(v,pid,factor))

    def tearDown(self): self.temp.cleanup()

    def add_order(self,c,oid,variation,qty,status='paid',table='marketplace_order_inbox',updated='2026-10-25T13:00:00-03:00',created='2026-10-25T12:00:00-03:00'):
        payload=json.dumps(dict(status=status,order_items=[dict(item=dict(id='MLA1',variation_id=variation),quantity=qty)]))
        c.execute(f"INSERT INTO {table}(marketplace,order_id,date_created,date_last_updated,payload_json,fetched_at) VALUES('MERCADOLIBRE',?,?,?,?,?)",(oid,created,updated,payload,updated))

    def test_exact_variants_pack_conversion_latest_cancellation_and_closed_days(self):
        with self.db.transaction() as c:
            self.add_order(c,'1','red',3)
            self.add_order(c,'2','green',2)
            self.add_order(c,'3','red',100)
            self.add_order(c,'3','red',100,status='cancelled',table='marketplace_audit_orders',updated='2026-10-25T14:00:00-03:00')
            self.add_order(c,'4','red',500,created='2026-10-26T01:00:00-03:00')
            self.add_order(c,'5','unknown',1)
        with self.db.session() as c:
            c.execute('PRAGMA query_only=ON')
            r=stock_objectives(c,self.now)
        self.assertEqual(r[self.pids[0]]['sales'][7],150)
        self.assertEqual(r[self.pids[1]]['sales'][7],200)
        self.assertTrue(any('sin asociación' in e for e in r[self.pids[0]]['issues']))
        self.assertEqual(r[self.pids[0]]['sales_through'],'2026-10-25')

    def test_portal_adds_columns_without_database_changes(self):
        before=self.path.read_bytes()
        client=create_app(dict(TESTING=True,DATABASE_PATH=str(self.path))).test_client()
        for path in ('/bolsas','/rollos','/bolsas?color=Rojas'):
            response=client.get(path)
            self.assertEqual(response.status_code,200)
            page=response.get_data(as_text=True)
            self.assertIn('Objetivo recomendado',page)
            self.assertIn('Stock objetivo actual',page)
        self.assertIn('Sin ventas verificadas',client.get('/bolsas').get_data(as_text=True))
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(client.post('/bolsas').status_code,405)


if __name__=='__main__': unittest.main()
