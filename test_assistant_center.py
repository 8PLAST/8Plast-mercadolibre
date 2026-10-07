import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from assistant_center import (ReadOnlyMercadoLibre, ReviewStore, suggest, QueryFailure,
                              fetch_questions, integration_token_provider, register_center)
from assistant_preview import create_app


def payload(qid='1', item='MLA123', pack='25'):
    return {'questions':[{'id':qid,'item_id':item,'text':'Cuántas unidades trae el pack?', 'status':'UNANSWERED'}],
            'publications':{item:{'title':'Bolsas','attributes':[{'id':'UNITS_PER_PACK','value_name':pack}]}}}


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'reviews.db'
        self.store = ReviewStore(self.path)

    def test_external_writes_and_arbitrary_paths_blocked_before_network(self):
        client = ReadOnlyMercadoLibre('secret')
        with patch('urllib.request.build_opener') as network:
            for method in ('POST','PUT','PATCH','DELETE'):
                with self.assertRaises(PermissionError): client.request('/answers', method, {})
            for path in ('https://evil.test','/orders/123','/items/MLA123/../answers','/users/me?token=secret'):
                with self.assertRaises(PermissionError): client.request(path)
            network.assert_not_called()

    def test_decisions_preserve_original_correction_and_approved_no_network(self):
        self.store.ingest(payload(), 'test')
        with patch('urllib.request.build_opener') as network:
            self.store.decide('1','EDITADA','Hola, son 25 bolsas.',1)
            self.store.decide('1','APROBADA','Hola, son 25 bolsas.',2)
            network.assert_not_called()
        self.store.ingest(payload(), 'reimport')
        with self.store.connect() as con:
            row = con.execute('SELECT * FROM questions').fetchone()
            self.assertEqual(row['approved'],'Hola, son 25 bolsas.')
            self.assertNotEqual(row['original'], row['approved'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],2)
        with self.assertRaises(ValueError): self.store.decide('1','RECHAZADA','',1)

    def test_packs_never_combined(self):
        self.store.ingest(payload(), 'test')
        self.store.ingest(payload('2','MLA456','100'), 'test')
        with self.store.connect() as con:
            drafts = [r['original'] for r in con.execute('SELECT * FROM questions ORDER BY id')]
        self.assertIn('25',drafts[0]); self.assertIn('100',drafts[1]); self.assertNotIn('100',drafts[0])

    def test_no_unverified_claims(self):
        draft, used, missing = suggest('Llega mañana y resiste 50 kilos?',{'logistica':'Full','usos':'resistente'})
        self.assertFalse(used); self.assertTrue(missing); self.assertNotIn('Full',draft)

    def test_atomic_import_and_external_answer_blocks_approval(self):
        bad=payload(); bad['questions'].append({'id':'bad'})
        with self.assertRaises(ValueError): self.store.ingest(bad,'test')
        with self.store.connect() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM questions').fetchone()[0],0)
        answered=payload(); answered['questions'][0]['status']='ANSWERED'
        self.store.ingest(answered,'test')
        with self.assertRaises(ValueError): self.store.decide('1','APROBADA','Respuesta',1)

    def test_ui_csrf_escaping_and_import(self):
        app=create_app(self.path); app.testing=True
        client=app.test_client()
        self.assertEqual(client.get('/asistentes').status_code,200)
        self.assertEqual(client.post('/asistentes/decision/1').status_code,403)
        p=payload(); p['questions'][0]['text']='<script>alert(1)</script>'
        self.store.ingest(p,'test')
        page=client.get('/asistentes').get_data(as_text=True)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert',page)
        with client.session_transaction() as session: csrf=session['csrf']
        response=client.post('/asistentes/decision/1',data={'csrf':csrf,'action':'EDITADA','version':'1','text':'Corregida'})
        self.assertEqual(response.status_code,302)

    def test_preview_reports_missing_cloud_connection_without_network(self):
        app=create_app(self.path); app.testing=True
        client=app.test_client(); client.get('/asistentes')
        with client.session_transaction() as session: csrf=session['csrf']
        with patch('urllib.request.build_opener') as network:
            with self.assertLogs(app.logger, level='WARNING') as logs:
                client.post('/asistentes/consultar',data={'csrf':csrf})
            network.assert_not_called()
        page=client.get('/asistentes').get_data(as_text=True)
        self.assertIn('CONFIGURATION_MISSING',page)
        self.assertIn('Sin consultas exitosas',page)
        self.assertNotIn('exitosa: None',page)
        self.assertIn('CONFIGURATION_MISSING', '\n'.join(logs.output))

    def test_http_errors_are_classified_and_redacted(self):
        expected={401:'TOKEN_EXPIRED',403:'PERMISSION_DENIED',404:'NOT_FOUND',429:'RATE_LIMIT',500:'API_UNAVAILABLE',400:'API_REQUEST'}
        for status, code in expected.items():
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=urllib.error.HTTPError('https://secret',status,'sensitive body',{},None)
                with self.assertRaises(QueryFailure) as raised:
                    ReadOnlyMercadoLibre('SENSITIVE_TOKEN').request('/users/me')
                self.assertEqual(raised.exception.code,code)
                self.assertNotIn('SENSITIVE',raised.exception.public_message())
                self.assertNotIn('secret',raised.exception.public_message())

    def test_rejected_token_reuses_refresh_and_retries_once(self):
        from unittest.mock import Mock
        provider=Mock(return_value='fresh')
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"id": 123}'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=[urllib.error.HTTPError('url',401,'no',{},None),response]
            self.assertEqual(ReadOnlyMercadoLibre('old',provider).request('/users/me'),{'id':123})
            provider.assert_called_once_with(rejected_token='old')
            requests=[c.args[0] for c in opener.return_value.open.call_args_list]
            self.assertEqual([r.get_method() for r in requests],['GET','GET'])
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=urllib.error.HTTPError('url',401,'no',{},None)
            with self.assertRaises(QueryFailure): ReadOnlyMercadoLibre('old',provider).request('/users/me')
            self.assertEqual(opener.return_value.open.call_count,2)

    def test_cloud_auth_reuses_existing_token_method_and_never_local(self):
        from unittest.mock import Mock
        client=Mock(); client.secure_config.return_value={'access_token':'old'}
        client.access_token.return_value='fresh'
        provider=integration_token_provider(client)
        with patch('runtime_config.cloud_mode',return_value=False):
            with self.assertRaises(QueryFailure) as raised: provider()
            self.assertEqual(raised.exception.code,'CONFIGURATION_MISSING')
            client.secure_config.assert_not_called()
        with patch('runtime_config.cloud_mode',return_value=True), patch('runtime_config.sync_owner',return_value=True):
            self.assertEqual(provider(rejected_token='old'),'fresh')
            client.access_token.assert_called_once_with(rejected_token='old')

    def test_real_shape_questions_load_even_when_item_forbidden(self):
        def api(path):
            if path=='/users/me': return {'id':123}
            if path.startswith('/questions/search?'): return {'questions':payload()['questions']}
            raise QueryFailure('PERMISSION_DENIED', '/items/MLA123',403)
        with patch.object(ReadOnlyMercadoLibre,'request',side_effect=api):
            data,warnings=fetch_questions(lambda **kwargs:'token')
        self.assertTrue(warnings)
        self.store.ingest(data,'API test')
        with self.store.connect() as con:
            q=con.execute('SELECT * FROM questions').fetchone()
            self.assertEqual(q['id'],'1'); self.assertEqual(json.loads(q['facts']),{})

    def test_cloud_route_fetches_and_approval_does_not_connect(self):
        from flask import Flask
        app=Flask(__name__,template_folder='readonly_portal/templates'); app.secret_key='test'
        register_center(app,self.path,token_provider=lambda **kwargs:'token')
        with patch('assistant_center.fetch_questions',return_value=(payload(),[])) as fetch:
            response=app.test_client().post('/asistentes/consultar')
            self.assertEqual(response.status_code,302); fetch.assert_called_once()
        page=app.test_client().get('/asistentes').get_data(as_text=True)
        self.assertIn('Cuántas unidades',page)
        with patch('assistant_center.fetch_questions') as fetch, patch('urllib.request.build_opener') as network:
            app.test_client().post('/asistentes/decision/1',data={'action':'APROBADA','text':'25 bolsas','version':'1'})
            fetch.assert_not_called(); network.assert_not_called()

    def test_failed_query_preserves_last_success_and_redacts_raw_exception(self):
        from flask import Flask
        self.store.ingest(payload(),'previous')
        with self.store.connect() as con:
            before=con.execute('SELECT success FROM sync').fetchone()[0]
        app=Flask(__name__); app.secret_key='test'
        register_center(app,self.path,token_provider=lambda **kwargs:'token')
        with patch('assistant_center.fetch_questions',side_effect=RuntimeError('SENSITIVE_SECRET')):
            with self.assertLogs(app.logger,level='WARNING') as logs:
                app.test_client().post('/asistentes/consultar')
        with self.store.connect() as con:
            row=con.execute('SELECT * FROM sync').fetchone()
            self.assertEqual(row['success'],before)
            self.assertIn('INTERNAL',row['error'])
            self.assertNotIn('SENSITIVE_SECRET',row['error'])
            self.assertIsNotNone(row['attempted'])
        self.assertNotIn('SENSITIVE_SECRET','\n'.join(logs.output))

    def test_missing_questions_is_invalid_response_not_empty_success(self):
        with patch.object(ReadOnlyMercadoLibre,'request',side_effect=[{'id':123},{'error':'secret'}]):
            with self.assertRaises(QueryFailure) as raised: fetch_questions(lambda **kwargs:'token')
            self.assertEqual(raised.exception.code,'INVALID_RESPONSE')


if __name__ == '__main__': unittest.main()
