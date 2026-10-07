import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant_center import ReviewStore, QueryFailure
import assistant_automation as auto


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.tmp.name)/'reviews.db')
        auto.initialize(self.store)
        with self.store.connect() as con:
            con.execute('UPDATE assistant_monitor SET enabled=1,chat_id=? WHERE id=1',('123',))

    def tearDown(self): self.tmp.cleanup()

    def payload(self, qid='1', date='2026-10-07T10:00:00+00:00'):
        return {'questions':[{'id':qid,'item_id':'MLA123','text':'Cuántas unidades trae?',
            'status':'UNANSWERED','date_created':date}],
            'publications':{'MLA123':{'title':'Bolsa','attributes':[{'id':'UNITS_PER_PACK','value_name':'50'}]}}}

    def read(self, table):
        with self.store.connect() as con: return [dict(r) for r in con.execute('SELECT * FROM '+table)]

    def test_baseline_silent_future_only_and_deduplicated_after_restart(self):
        with patch.object(auto,'fetch_questions',return_value=(self.payload(),[])), patch.object(auto,'now',return_value='2026-10-07T11:00:00+00:00'):
            auto.poll(self.store,lambda:'token')
        self.assertEqual(self.read('assistant_alerts'),[])
        new = self.payload('2','2026-10-07T11:01:00+00:00')
        new['questions'] += self.payload()['questions']
        with patch.object(auto,'fetch_questions',return_value=(new,[])):
            auto.poll(self.store,lambda:'token')
            auto.poll(ReviewStore(self.store.path),lambda:'token')
        self.assertEqual([r['question_id'] for r in self.read('assistant_alerts')],['2'])
        with patch.object(auto,'telegram',return_value={'message_id':7}) as send:
            auto.dispatch(self.store); auto.dispatch(ReviewStore(self.store.path))
        self.assertEqual(send.call_count,1)
        text = send.call_args[0][1]['text']
        self.assertIn('50',text); self.assertIn(auto.PANEL+'#q-2',text)

    def test_failure_not_empty_success_or_baseline(self):
        with patch.object(auto,'fetch_questions',side_effect=QueryFailure('POLICY_DENIED','/my/received_questions/search',403)):
            auto.tick(self.store,lambda:'token')
        state = self.read('assistant_monitor')[0]
        self.assertIsNone(state['baseline']); self.assertIsNone(state['last_success'])
        self.assertIn('403',state['error']); self.assertEqual(self.read('questions'),[])
        with patch.object(auto,'fetch_questions',return_value=({'questions':[],'publications':{}},[])):
            auto.poll(self.store,lambda:'token')
        self.assertIsNotNone(self.read('assistant_monitor')[0]['last_success'])
        self.assertIsNone(self.read('assistant_monitor')[0]['error'])

    def test_private_one_time_pairing_rejects_strangers_and_groups(self):
        with patch.object(auto,'telegram',return_value={'username':'example_bot'}):
            link = auto.begin_pairing(self.store)
        code = link.split('=')[1]
        updates = [ {'update_id':i,'message':{'chat':{'type':kind,'id':recipient},'text':text}}
            for i,kind,recipient,text in [(1,'private',999,'/start wrong'),(2,'group',555,'/start '+code),(3,'private',42,'/start '+code),(4,'private',43,'/start '+code)] ]
        with patch.object(auto,'telegram',return_value=updates): auto.check_pairing(self.store)
        self.assertEqual(self.read('assistant_monitor')[0]['chat_id'],'42')
        self.assertIsNone(self.read('assistant_monitor')[0]['pair_hash'])
        self.assertNotIn('chat_id',auto.status(self.store)[0])

    def test_unknown_delivery_never_automatically_retried(self):
        testid = auto.queue_test(self.store)
        with patch.object(auto,'telegram',side_effect=auto.ServiceFailure('DELIVERY_UNKNOWN')) as send:
            auto.dispatch(self.store); auto.dispatch(self.store)
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.read('assistant_alerts')[0]['state'],'UNKNOWN')
        self.assertEqual(auto.queue_test(self.store),testid)

    def test_rate_limit_retries_only_after_wait(self):
        auto.queue_test(self.store)
        with patch.object(auto,'telegram',side_effect=auto.ServiceFailure('HTTP_429',60)) as send:
            auto.dispatch(self.store); auto.dispatch(self.store)
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.read('assistant_alerts')[0]['state'],'PENDING')

    def test_ai_cannot_invent_facts_and_does_not_overwrite_human_changes(self):
        self.store.ingest(self.payload(),'test')
        result = {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'facts':['unidades_pack'],'needs_review':False})}]}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret-test'}),patch.object(auto,'post_json',return_value=result) as api:
            auto.draft_question(self.store,'1')
        q = self.read('questions')[0]
        self.assertEqual(q['original'],'Hola. Unidades por pack: 50.')
        self.assertEqual(api.call_args[0][1]['store'],False)
        self.store.decide('1','EDITADA','Mi corrección',q['version'])
        with patch.object(auto,'ai_draft',side_effect=AssertionError('no llamada')):
            auto.draft_question(self.store,'1')
        self.assertEqual(self.read('questions')[0]['edited'],'Mi corrección')
        result['output'][0]['content'][0]['text']=json.dumps({'facts':['medida'],'needs_review':False})
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret-test'}),patch.object(auto,'post_json',return_value=result):
            with self.assertRaises(auto.ServiceFailure): auto.ai_draft('medidas',{'unidades_pack':'50'})

    def test_concurrent_edit_is_preserved_during_ai(self):
        self.store.ingest(self.payload(),'test')
        def generate(*args):
            self.store.decide('1','APROBADA','Decisión humana',1)
            return 'Otro borrador',{},[]
        with patch.object(auto,'ai_draft',side_effect=generate): auto.draft_question(self.store,'1')
        q=self.read('questions')[0]
        self.assertEqual(q['approved'],'Decisión humana')
        self.assertEqual(self.read('assistant_drafts')[0]['state'],'PRESERVED')

    def test_test_identified_and_no_external_customer_capability(self):
        auto.queue_test(self.store)
        with patch.object(auto,'telegram',return_value={'message_id':5}) as send:
            auto.dispatch(self.store)
        self.assertTrue(send.call_args[0][1]['text'].startswith('PRUEBA'))
        self.assertIn('NO verificados',send.call_args[0][1]['text'])
        with self.assertRaises(ValueError): auto.telegram('answerQuestion',{})

    def test_item_policy_denial_does_not_hide_real_questions(self):
        from assistant_center import fetch_questions
        def request(path):
            if path=='/users/me': return {'id':123}
            if path.startswith('/my/received_questions/search'): return {'questions':self.payload()['questions']}
            raise QueryFailure('POLICY_DENIED',path,403)
        with patch('assistant_center.ReadOnlyMercadoLibre.request',side_effect=request):
            payload,warnings=fetch_questions(lambda **kw:'token')
        self.assertEqual(len(payload['questions']),1)
        self.assertEqual(payload['publications']['MLA123'],{})
        self.assertIn('POLICY_DENIED',warnings[0])

    def test_new_product_evidence_updates_without_losing_approval(self):
        self.store.ingest(self.payload(),'test')
        self.store.decide('1','APROBADA','Aprobación humana',1)
        changed=self.payload()
        changed['publications']['MLA123']['attributes'][0]['value_name']='100'
        changed['publications']['MLA123']['title']='Título verificado'
        self.store.ingest(changed,'refresh')
        q=self.read('questions')[0]
        self.assertEqual(q['approved'],'Aprobación humana')
        self.assertEqual(q['edited'],'Aprobación humana')
        self.assertEqual(json.loads(q['facts'])['unidades_pack'],'100')
        self.assertEqual(q['title'],'Título verificado')


if __name__=='__main__': unittest.main()
