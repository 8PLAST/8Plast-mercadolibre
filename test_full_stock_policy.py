import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from db import Database, StockError
from full_stock_policy import activate_once, activation, policy_path
from ml_integration import MercadoLibreClient, MercadoLibreError
from stock_objectives import stock_objectives
from marketplace_analytics import marketplace_statistics


class FullPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/'test.db')
        self.pid=self.db.add_product(dict(kind='BAG',sku='FULL-TEST',name='Bolsa',width_cm=50,length_cm=70,microns=35),initial_stock=1000)
        self.db.add_marketplace_listing(self.pid,'MLA123',50)
        with self.db.session() as c:
            c.execute("UPDATE marketplace_listings SET sync_from='2026-01-01T00:00:00+00:00'")
        policy_path(self.db.path).write_text(json.dumps({'effective_from':'2026-10-26T21:00:00+00:00'}),encoding='utf-8')

    def tearDown(self): self.temp.cleanup()

    def order(self,oid='123',created='2026-10-27T10:00:00+00:00'):
        return dict(id=oid,status='paid',date_created=created,shipping={'id':456},
                    order_items=[dict(item=dict(id='MLA123'),quantity=2)])

    def test_full_never_deducts_and_stays_in_commercial_demand(self):
        o=self.order(); self.db.store_marketplace_orders([o])
        for _ in range(2):
            result=self.db.process_marketplace_order(o,lambda order:'fulfillment')
            self.assertEqual(result['full_skipped'],1)
        self.assertEqual(self.db.product(self.pid)['stock'],1000)
        with self.db.session() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM stock_movements WHERE source='MERCADOLIBRE'").fetchone()[0],0)
            now=datetime(2026,10,28,12,tzinfo=timezone.utc)
            self.assertEqual(marketplace_statistics(c,now)[0][self.pid]['sales_30'],100)
            self.assertEqual(stock_objectives(c,now)[self.pid]['sales'][90],100)

    def test_flex_and_own_dispatch_keep_pack_deduction_and_idempotency(self):
        for i,logistics in enumerate(('self_service','cross_docking','drop_off','xd_drop_off','default','custom','turbo')):
            o=self.order(str(100+i));self.db.process_marketplace_order(o,lambda order:logistics)
            self.db.process_marketplace_order(o,lambda order:logistics)
        self.assertEqual(self.db.product(self.pid)['stock'],300)

    def test_missing_logistics_never_defaults_to_depot_and_can_retry(self):
        o=self.order(); self.db.store_marketplace_orders([o])
        result=self.db.process_marketplace_inbox(lambda order: (_ for _ in ()).throw(MercadoLibreError('unavailable')))
        self.assertEqual(len(result['errors']),1)
        self.assertEqual(self.db.product(self.pid)['stock'],1000)
        self.db.retry_marketplace_inbox_errors()
        result=self.db.process_marketplace_inbox(lambda order:'self_service')
        self.assertEqual(result['processed'],1)
        self.assertEqual(self.db.product(self.pid)['stock'],900)
        with self.assertRaises(StockError): self.db.process_marketplace_order(self.order('456'))
        with self.assertRaises(StockError): self.db.process_marketplace_order(self.order('789'),lambda o:'not_specified')

    def test_historical_movement_is_never_reversed_or_reclassified(self):
        o=self.order(created='2026-10-26T20:59:59+00:00')
        self.db.process_marketplace_order(o)
        with self.db.session() as c: before=[tuple(r) for r in c.execute('SELECT * FROM stock_movements ORDER BY id')]
        resolver=lambda order: self.fail('La regla no debe consultar ventas previas a la vigencia')
        self.db.process_marketplace_order(o,resolver)
        with self.db.session() as c: after=[tuple(r) for r in c.execute('SELECT * FROM stock_movements ORDER BY id')]
        self.assertEqual(before,after)
        self.assertEqual(self.db.product(self.pid)['stock'],900)

    def test_boundary_is_inclusive_and_persists_restarts(self):
        first=activation(self.db.path)
        self.assertEqual(activate_once(self.db.path),first)
        o=self.order(created='2026-10-26T21:00:00+00:00')
        self.assertEqual(self.db.process_marketplace_order(o,lambda order:'fulfillment')['full_skipped'],1)
        self.assertEqual(self.db.product(self.pid)['stock'],1000)

    def test_recovery_processes_full_through_same_resolver(self):
        self.db.store_marketplace_orders([self.order()])
        client=MercadoLibreClient(self.db)
        with patch.object(client,'_recover_stream',return_value={}),patch.object(client,'resolve_shipping_logistics',return_value='fulfillment') as resolve:
            summary=client.sync_orders_recovery()
        resolve.assert_called_once()
        self.assertEqual(summary['processing']['full_skipped'],1)
        with self.db.session() as c:
            self.assertEqual(c.execute('SELECT processing_status FROM marketplace_order_inbox').fetchone()[0],'FULL_NO_STOCK')
        self.assertEqual(self.db.product(self.pid)['stock'],1000)


class ShippingApiTests(unittest.TestCase):
    def test_reads_shipment_not_current_listing_and_nested_logistics(self):
        client=MercadoLibreClient(None)
        with patch.object(client,'access_token',return_value='test'),patch.object(client,'_request',return_value={'id':456,'logistic':{'type':'fulfillment','direction':'forward'}}) as request:
            self.assertEqual(client.resolve_shipping_logistics({'id':123,'shipping':{'id':456}}),'fulfillment')
            self.assertEqual(request.call_args.args[0],'https://api.mercadolibre.com/shipments/456')

    def test_missing_id_resolves_forward_not_return(self):
        client=MercadoLibreClient(None)
        responses=[[{'id':987,'type':'return'},{'id':456,'type':'forward'}],{'id':456,'logistic_type':'self_service'}]
        with patch.object(client,'access_token',return_value='test'),patch.object(client,'_request',side_effect=responses) as request:
            self.assertEqual(client.resolve_shipping_logistics({'id':123}),'self_service')
            self.assertEqual(request.call_args_list[0].args[0],'https://api.mercadolibre.com/orders/123/shipments')
            self.assertEqual(request.call_args_list[1].args[0],'https://api.mercadolibre.com/shipments/456')

    def test_wrong_or_conflicting_shipment_does_not_classify(self):
        client=MercadoLibreClient(None)
        for response in ({'id':999,'logistic_type':'fulfillment'}, {'id':456,'logistic_type':'fulfillment','logistic':{'type':'self_service'}}, {'id':456,'logistic':{'type':'fulfillment','direction':'return'}}, {'id':456}):
            with patch.object(client,'access_token',return_value='test'),patch.object(client,'_request',return_value=response):
                with self.assertRaises(MercadoLibreError): client.resolve_shipping_logistics({'shipping':{'id':456}})

    def test_required_headers_and_get_only(self):
        client=MercadoLibreClient(None)
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{}'
        with patch('ml_integration.cloud_mode',return_value=False),patch('urllib.request.urlopen',return_value=Response()) as network:
            client._request('https://api.mercadolibre.com/shipments/456',token='test')
            request=network.call_args.args[0]
            self.assertEqual(request.get_method(),'GET')
            self.assertEqual(dict((k.lower(),v) for k,v in request.header_items())['x-format-new'],'true')
            client._request('https://api.mercadolibre.com/orders/123/shipments',token='test')
            self.assertEqual(dict((k.lower(),v) for k,v in network.call_args.args[0].header_items())['x-new-domain'],'true')


if __name__=='__main__': unittest.main()
