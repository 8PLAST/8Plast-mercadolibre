import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from db import Database
from ml_integration import MercadoLibreClient, MercadoLibreError
from runtime_config import file_lock

class CloudTests(unittest.TestCase):
    def test_deployment_commands_do_not_expand_port_in_shell(self):
        root=Path(__file__).resolve().parent
        self.assertNotIn('$PORT',(root/'render.yaml').read_text())
        self.assertIn('ENTRYPOINT ["python", "/app/cloud_start.py"]',(root/'Dockerfile').read_text())
        self.assertEqual(json.loads((root/'railway.json').read_text())['deploy']['startCommand'],'python cloud_start.py')

    def test_port_is_numeric_and_not_a_shell_placeholder(self):
        from cloud_web import listen_port
        for value in ('$PORT','${PORT}','0','65536','abc',''):
            with patch.dict(os.environ,{'PORT':value}):
                with self.assertRaises(RuntimeError): listen_port()
        with patch.dict(os.environ,{'PORT':' 43210 '}):
            self.assertEqual(listen_port(),43210)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'stock.db'
        self.env=patch.dict(os.environ,{'APP_ENV':'cloud','DATABASE_PATH':str(self.path),'WEBHOOK_DATABASE_PATH':str(self.path),
            'PORTAL_DATABASE_PATH':str(self.path),'MELI_SYNC_ENABLED':'0','MELI_TOKEN_KEY':Fernet.generate_key().decode(),
            'PORTAL_SECRET_KEY':'test-session-key-'*4,'PORTAL_PASSWORD':'test-password-123456789',
            'MELI_CLIENT_ID':'test-client','MELI_CLIENT_SECRET':'test-secret','MELI_REDIRECT_URI':'https://test.local/oauth/callback'})
        self.env.start()
        from cloud_app import create_app
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(self.path)}); self.db=self.app.extensions['stock_db']
        self.web=self.app.test_client()

    def tearDown(self): self.env.stop(); self.tmp.cleanup()

    def login(self):
        self.web.get('/login',base_url='https://localhost')
        with self.web.session_transaction() as s: csrf=s['csrf']
        r=self.web.post('/login',data={'csrf':csrf,'password':os.environ['PORTAL_PASSWORD']},base_url='https://localhost')
        self.assertEqual(r.status_code,302)

    def post(self,url,data=None):
        with self.web.session_transaction() as s: csrf=s['csrf']
        return self.web.post(url,data={'csrf':csrf,**(data or {})},base_url='https://localhost')

    def product(self):
        return self.db.add_product({'kind':'BAG','sku':'TEST-CLOUD','name':'Bolsa de prueba','width_cm':50,'length_cm':70,'microns':35},10)

    def test_portal_authentication_csrf_and_standby(self):
        self.assertEqual(self.web.get('/movimientos').status_code,302)
        self.login()
        self.assertEqual(self.web.get('/gestion',base_url='https://localhost').status_code,200)
        self.assertEqual(self.web.post('/gestion/accion',data={'action':'audit'},base_url='https://localhost').status_code,403)
        self.assertEqual(self.post('/gestion/accion',{'action':'audit'}).status_code,409)
        self.assertEqual(self.post('/oauth/start').status_code,409)

    def test_movement_form_is_idempotent_and_portal_shows_it(self):
        pid=self.product(); os.environ['MELI_SYNC_ENABLED']='1'; self.login()
        data={'action':'movement','product_id':str(pid),'amount':'2','movement_type':'Venta','reason':'Venta web','request_key':'same-submit'}
        self.assertEqual(self.post('/gestion/accion',data).status_code,302)
        self.post('/gestion/accion',data)
        self.assertEqual(self.db.product(pid)['stock'],8)
        self.assertIn(b'Venta web',self.web.get('/movimientos',base_url='https://localhost').data)

    def test_inventory_page_and_stock_actions(self):
        self.assertEqual(self.web.get('/agregar-inventario').status_code,302)
        pid=self.product(); os.environ['MELI_SYNC_ENABLED']='1'; self.login()
        page=self.web.get('/agregar-inventario',base_url='https://localhost').get_data(as_text=True)
        self.assertIn('Agregar inventario',page)
        self.assertIn('TEST-CLOUD',page)
        self.assertNotIn('http-equiv="refresh"',page)
        data={'action':'movement','product_id':str(pid),'amount':'20','movement_type':'Entrada manual','reason':'Ingreso','request_key':'inventory-entry','return_to':'inventory'}
        result=self.post('/gestion/accion',data)
        self.assertEqual(result.location,'/agregar-inventario')
        self.assertEqual(self.db.product(pid)['stock'],30)
        self.post('/gestion/accion',{'action':'adjust','product_id':str(pid),'target':'15','reason':'Conteo','request_key':'inventory-count','return_to':'inventory'})
        self.assertEqual(self.db.product(pid)['stock'],15)

    def test_product_and_association_forms(self):
        os.environ['MELI_SYNC_ENABLED']='1'; self.login()
        data={'action':'product','kind':'ROLL','sku':'ROLL-TEST','name':'Rollo prueba','width_cm':'60','microns':'50','meters_per_roll':'100','minimum_stock':'1','target_stock':'10','storage_pack':'50','initial_stock':'3'}
        self.post('/gestion/accion',data); pid=self.db.products()[0]['id']
        self.post('/gestion/accion',{'action':'listing','product_id':str(pid),'listing_id':'MLA999','units_consumed':'2'})
        self.assertEqual(self.db.marketplace_listing('MLA999')['units_consumed'],2)
        data.update(product_id=str(pid),name='Nombre editado',active='0')
        self.post('/gestion/accion',data)
        self.assertEqual(self.db.product(pid)['active'],0)

    def test_refresh_is_encrypted_persistent_and_uses_rotated_token(self):
        os.environ['MELI_SYNC_ENABLED']='1'
        from cloud_tokens import write_tokens,read_tokens
        write_tokens({'access_token':'expired','refresh_token':'old-refresh','expires_at':0,'user_id':123})
        client=MercadoLibreClient(self.db)
        with patch.object(client,'_request',return_value={'access_token':'fresh','refresh_token':'rotated','expires_in':21600,'user_id':123}) as req:
            self.assertEqual(client.access_token(),'fresh')
            self.assertEqual(req.call_args.args[2]['refresh_token'],'old-refresh')
        self.assertEqual(MercadoLibreClient(self.db).access_token(),'fresh')
        self.assertEqual(read_tokens()['refresh_token'],'rotated')
        self.assertNotIn(b'rotated',(self.path.parent/'meli_tokens.enc').read_bytes())
        state=read_tokens(); state['expires_at']=0; write_tokens(state)
        with patch.object(client,'_request',return_value={'access_token':'fresh2','refresh_token':'rotated2','expires_in':21600}) as req:
            client.access_token(); self.assertEqual(req.call_args.args[2]['refresh_token'],'rotated')

    def test_standby_never_contacts_ml(self):
        client=MercadoLibreClient(self.db)
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(MercadoLibreError): client._request('https://api.mercadolibre.com/users/me')
            network.assert_not_called()

    def test_webhook_to_stock_once_and_seller_validation(self):
        os.environ['MELI_SYNC_ENABLED']='1'; pid=self.product()
        self.db.add_marketplace_listing(pid,'MLA999',25)
        self.db.configure_marketplace_processing(True,300,'2020-01-01T00:00:00Z')
        with self.db.session() as con: con.execute("UPDATE marketplace_listings SET sync_from='2020-01-01T00:00:00Z'")
        payload={'topic':'orders_v2','resource':'/orders/123','user_id':456,'sent':'2026-09-01T00:00:00Z'}
        self.assertEqual(self.web.post('/webhook/mercadolibre',json=payload).status_code,200)
        self.web.post('/webhook/mercadolibre',json=payload)
        from mercadolibre_worker import process_notifications
        order={'id':123,'seller':{'id':456},'date_created':'2026-09-01T00:00:00Z','status':'paid','order_items':[{'item':{'id':'MLA999'},'quantity':2}]}
        client=MercadoLibreClient(self.db)
        with patch.object(client,'_seller_id',return_value=456),patch.object(client,'get_order',return_value=order):
            process_notifications(self.db,client); self.db.process_marketplace_inbox()
            process_notifications(self.db,client); self.db.process_marketplace_inbox()
        self.assertEqual(self.db.product(pid)['stock'],-40)
        self.assertEqual(Database(self.path).product(pid)['stock'],-40)
        self.assertEqual(len([m for m in self.db.movements() if m['source']=='MERCADOLIBRE']),1)

    def test_import_preserves_existing_cloud_notifications_and_rejects_overwrite(self):
        from cloud_import import import_snapshot
        source=Database(self.path.parent/'source.db')
        source.add_product({'kind':'BAG','sku':'IMPORTED','name':'Importado','width_cm':10,'length_cm':20,'microns':35},100)
        self.web.post('/webhook/mercadolibre',json={'topic':'orders_v2','resource':'/orders/55','user_id':1})
        import_snapshot(self.db,Path(source.path))
        self.assertEqual(self.db.products()[0]['stock'],100)
        with self.db.session() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM mercadolibre_webhook_events').fetchone()[0],1)
        with self.assertRaises(ValueError): import_snapshot(self.db,Path(source.path))
        self.assertEqual(self.db.products()[0]['stock'],100)

    def test_file_lock_excludes_second_worker(self):
        with file_lock(self.path.parent/'lock'):
            with self.assertRaises(TimeoutError):
                with file_lock(self.path.parent/'lock'): pass

    def test_oauth_state_required(self):
        os.environ['MELI_SYNC_ENABLED']='1'; self.login()
        self.assertEqual(self.web.get('/oauth/callback?code=fake',base_url='https://localhost').status_code,400)
        self.assertEqual(self.post('/oauth/start').status_code,302)
        from cloud_tokens import read_tokens
        self.assertTrue(read_tokens()['oauth_state'])
        self.assertEqual(self.web.get('/oauth/callback?code=fake&state=wrong',base_url='https://localhost').status_code,400)

    def test_health_checks_worker(self):
        self.assertEqual(self.web.get('/health').status_code,503)
        (self.path.parent/'worker_status.json').write_text(json.dumps({'time':time.time(),'state':'standby'}))
        self.assertEqual(self.web.get('/health').status_code,200)
        for state,stamp in [('error',time.time()),('running',time.time()-1000),('running',time.time()+1000)]:
            (self.path.parent/'worker_status.json').write_text(json.dumps({'time':stamp,'state':state}))
            self.assertEqual(self.web.get('/health').status_code,503)

    def test_railway_requires_volume_and_database_inside_it(self):
        from runtime_config import validate_railway_storage
        with patch.dict(os.environ,{'RAILWAY_PROJECT_ID':'test','RAILWAY_VOLUME_MOUNT_PATH':''}):
            with self.assertRaises(RuntimeError): validate_railway_storage()
        with patch.dict(os.environ,{'RAILWAY_PROJECT_ID':'test','RAILWAY_VOLUME_MOUNT_PATH':'/data'}):
            with self.assertRaises(RuntimeError): validate_railway_storage()
            with patch.dict(os.environ,{k:'/data/8plast_stock.db' for k in ('DATABASE_PATH','WEBHOOK_DATABASE_PATH','PORTAL_DATABASE_PATH')}):
                validate_railway_storage()

    def test_adjustment_replay_does_not_override_later_movement(self):
        pid=self.product(); os.environ['MELI_SYNC_ENABLED']='1'; self.login()
        data={'action':'adjust','product_id':str(pid),'target':'20','reason':'Conteo','request_key':'adjust-once'}
        self.post('/gestion/accion',data)
        self.db.move_stock(pid,3,'Venta','Venta posterior')
        self.post('/gestion/accion',data)
        self.assertEqual(self.db.product(pid)['stock'],17)

    def test_read_only_views_work_in_authenticated_cloud(self):
        self.product(); self.login()
        for page in ('/','/bolsas','/rollos','/reposicion','/movimientos','/mercadolibre','/estadisticas','/gestion'):
            self.assertEqual(self.web.get(page,base_url='https://localhost').status_code,200,page)

    def test_cloud_worker_path_matches_database_path(self):
        from runtime_config import database_path
        self.assertEqual(database_path(),self.path)
        with patch.dict(os.environ,{'WEBHOOK_DATABASE_PATH':'/another/path.db'}):
            with self.assertRaises(RuntimeError): database_path()

    def test_database_conflict_does_not_touch_either_file(self):
        from runtime_config import database_path
        other=self.path.parent/'other.db'
        other.write_bytes(b'existing-data-must-not-change')
        original=self.path.read_bytes()
        with patch.dict(os.environ,{'PORTAL_DATABASE_PATH':str(other)}):
            with self.assertRaisesRegex(RuntimeError,'PORTAL_DATABASE_PATH'):
                database_path()
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(other.read_bytes(),b'existing-data-must-not-change')

    def test_database_paths_ignore_surrounding_whitespace(self):
        from runtime_config import database_path
        with patch.dict(os.environ,{'WEBHOOK_DATABASE_PATH':' '+str(self.path)+'\n'}):
            self.assertEqual(database_path(),self.path)

if __name__=='__main__': unittest.main()
