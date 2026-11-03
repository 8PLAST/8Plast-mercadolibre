import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant_center import ReviewStore
from assistant_knowledge import initialize, retrieve, save, metrics
import assistant_automation as auto
from assistant_publishing import publish


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ReviewStore(Path(self.tmp.name)/'review.db');auto.initialize(self.store)

    def add(self,qid='1',item='MLA123',text='Son aptas para freezer?',answer='',status='UNANSWERED'):
        self.store.ingest({'questions':[{'id':qid,'item_id':item,'text':text,'status':status,'answer':{'text':answer}}],
                          'publications':{item:{'title':'Bolsa','attributes':[]}}},'test')

    def row(self,table):
        with self.store.connect() as con:return dict(con.execute('SELECT * FROM '+table+' ORDER BY rowid DESC').fetchone())

    def test_exact_publication_identity_in_generation_context(self):
        self.add()
        self.assertEqual(retrieve(self.store,'1','MLA123','Consulta')['publication'],{'item_id':'MLA123','title':'Bolsa'})

    def test_entire_history_not_recent_hundred(self):
        for n in range(1,110):self.add(str(n),text='Cuántas unidades?',answer='100 unidades',status='ANSWERED')
        context=retrieve(self.store,'new','MLA123','Cuántas unidades?')
        self.assertEqual(context['history_scanned'],109)
        self.assertEqual(len(context['examples']),8)

    def test_correction_and_rejection(self):
        self.add(answer='Vieja',status='ANSWERED')
        self.store.decide('1','EDITADA','Corrección vigente',1)
        self.assertEqual(retrieve(self.store,'2','MLA123','freezer')['examples'][0]['answer'],'Corrección vigente')
        self.store.decide('1','RECHAZADA','No usar',2)
        self.assertTrue(retrieve(self.store,'2','MLA123','freezer')['examples'][0]['rejected'])

    def test_cross_product_style_only_and_no_answer_leakage(self):
        self.add(answer='Sí aptas',status='ANSWERED')
        ctx=retrieve(self.store,'1','MLA123','freezer')
        self.assertFalse(ctx['examples'])
        ctx=retrieve(self.store,'2','MLA999','Son aptas para freezer?')
        self.assertEqual(ctx['examples'][0]['authority'],'style_only')

    def test_versioned_knowledge_and_archived_answers(self):
        save(self.store,'MLA123','espesor','50 mic','Lucas')
        save(self.store,'MLA123','espesor','70 mic','Lucas',1)
        self.assertEqual(len(retrieve(self.store,'1','MLA123','mic')['knowledge']),1)
        with self.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM assistant_knowledge').fetchone()[0],2)
        self.add(answer='Primera',status='ANSWERED');self.add(answer='Segunda',status='ANSWERED')
        with self.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM assistant_answer_history').fetchone()[0],2)

    def response(self,ids=None):
        return {'status':'completed','usage':{'input_tokens':200,'output_tokens':40},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'answer':'¡Hola! ¿Qué cantidad necesitás embolsar?','evidence_ids':ids or [],'warnings':['Confirmar aptitud freezer.']})}]}]}

    def test_natural_answer_context_and_usage_saved(self):
        self.add();save(self.store,'MLA123','material','LDPE virgen','Lucas')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test','ASSISTANT_INPUT_USD_PER_M':'1','ASSISTANT_OUTPUT_USD_PER_M':'2'}),patch.object(auto,'post_json',return_value=self.response(['kb:1'])) as api:
            auto.draft_question(self.store,'1')
        self.assertEqual(self.row('assistant_drafts')['state'],'GENERATED')
        self.assertIn('¡Hola!',self.row('questions')['original'])
        self.assertAlmostEqual(self.row('assistant_attempts')['cost_usd'],.00028)
        self.assertIn('LDPE virgen',api.call_args[0][1]['input'])

    def test_bad_evidence_rejected(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(auto,'post_json',return_value=self.response(['kb:999'])):
            with self.assertRaises(auto.ServiceFailure):auto.ai_draft('Hola',{})

    def test_retry_wait_and_recovery_even_without_telegram_alert(self):
        self.add()
        with patch.object(auto,'ai_draft',side_effect=auto.ServiceFailure('HTTP_429',300)) as gen:
            auto.draft_question(self.store,'1');auto.draft_question(self.store,'1')
            self.assertEqual(gen.call_count,1)
        self.assertGreaterEqual(self.row('assistant_drafts')['retry_at'],time.time()+295)
        with self.store.connect() as con:con.execute('UPDATE assistant_drafts SET retry_at=0');con.execute('UPDATE assistant_ai_control SET retry_at=0')
        with patch.object(auto,'ai_draft',return_value=('Lista',{},[])):
            auto.draft_question(self.store,'1')
        self.assertEqual(self.row('assistant_drafts')['state'],'GENERATED')
        self.assertEqual(metrics(self.store)['recovered'],1)

    def test_crashed_lease_recovers_and_claim_deduplicates(self):
        self.add()
        with self.store.connect() as con:con.execute("INSERT INTO assistant_drafts(question_id,state,created,retry_at) VALUES('1','PROCESSING','old',0)")
        def concurrent(*args):
            with patch.object(auto,'ai_draft',side_effect=AssertionError('duplicate')):auto.draft_question(self.store,'1')
            return 'Lista',{},[]
        with patch.object(auto,'ai_draft',side_effect=concurrent):auto.draft_question(self.store,'1')
        self.assertEqual(self.row('assistant_drafts')['state'],'GENERATED')

    def test_real_exhausted_credit_response_is_not_retried_as_rate_limit(self):
        import io
        import urllib.error
        error=urllib.error.HTTPError('https://api.openai.com/v1/responses',429,'quota',{},io.BytesIO(json.dumps({'error':{'code':'credit_balance_exhausted','type':'insufficient_quota'}}).encode()))
        with patch('urllib.request.OpenerDirector.open',side_effect=error):
            with self.assertRaises(auto.ServiceFailure) as caught:
                auto.post_json('https://api.openai.com/v1/responses',{})
        self.assertEqual(caught.exception.code,'AI_QUOTA')

    def test_quota_blocks_until_manual_retry(self):
        self.add()
        with patch.object(auto,'ai_draft',side_effect=auto.ServiceFailure('AI_QUOTA')) as gen:
            auto.draft_question(self.store,'1');auto.draft_question(self.store,'1')
            self.assertEqual(gen.call_count,1)
        self.assertEqual(self.row('assistant_drafts')['state'],'BLOCKED')

    def test_worker_candidates_independent_of_alerts(self):
        self.add()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(auto,'check_pairing'),patch.object(auto,'dispatch'),patch.object(auto,'ai_draft',return_value=('Lista',{},[])):
            auto.tick(self.store,None,query=False)
        self.assertEqual(self.row('assistant_drafts')['state'],'GENERATED')

    def test_publish_disabled_and_2000_limit_before_network(self):
        self.add()
        with patch.dict(os.environ,{'ASSISTANT_PUBLISH_ENABLED':'0'}),patch('urllib.request.build_opener') as net:
            with self.assertRaises(ValueError):publish(self.store,'1','Sí',1,lambda:'test')
            net.assert_not_called()

    def test_explicit_publish_and_duplicates(self):
        self.add()
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'id':1,'status':'ANSWERED','answer':{'text':'Aprobada'}}).encode()
        def get(path):
            return {'id':9} if path=='/users/me' else {'id':1,'item_id':'MLA123','seller_id':9,'status':'UNANSWERED'}
        with patch.dict(os.environ,{'ASSISTANT_PUBLISH_ENABLED':'1'}),patch('assistant_publishing.ReadOnlyMercadoLibre.request',side_effect=get),patch('urllib.request.build_opener') as net:
            net.return_value.open.return_value=Response()
            self.assertEqual(publish(self.store,'1','Aprobada',1,lambda:'test'),'PUBLISHED')
            with self.assertRaises(ValueError):publish(self.store,'1','Aprobada',1,lambda:'test')
            self.assertEqual(net.return_value.open.call_count,1)

    def test_unknown_publish_never_replayed(self):
        self.add()
        def get(path):return {'id':9} if path=='/users/me' else {'id':1,'item_id':'MLA123','seller_id':9,'status':'UNANSWERED'}
        with patch.dict(os.environ,{'ASSISTANT_PUBLISH_ENABLED':'1'}),patch('assistant_publishing.ReadOnlyMercadoLibre.request',side_effect=get),patch('urllib.request.build_opener') as net:
            net.return_value.open.side_effect=TimeoutError()
            self.assertEqual(publish(self.store,'1','Aprobada',1,lambda:'test'),'UNKNOWN')
            with self.assertRaises(ValueError):publish(self.store,'1','Aprobada',2,lambda:'test')
            self.assertEqual(net.return_value.open.call_count,1)

if __name__=='__main__':unittest.main()
