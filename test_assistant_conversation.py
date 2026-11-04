import tempfile
import unittest
from pathlib import Path
from assistant_center import ReviewStore
from assistant_automation import initialize
from assistant_conversation import conversation, blind_snapshot


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ReviewStore(Path(self.tmp.name)/'review.db')
        initialize(self.store)

    def add(self, qid, buyer='10', item='MLA123', day='01', answer='Respuesta', answered_day=None):
        q = {'id':qid, 'item_id':item, 'from':{'id':buyer},
             'text':'Consulta '+qid, 'status':'ANSWERED' if answer else 'UNANSWERED',
             'date_created':f'2026-11-{day}T10:00:00Z'}
        if answer:
            q['answer'] = {'text':answer, 'date_created':f'2026-11-{answered_day or day}T10:05:00Z'}
        self.store.ingest({'questions':[q], 'publications':{}}, 'fixture')

    def test_only_same_buyer_and_product_and_prior_answer(self):
        self.add('1')
        self.add('2', buyer='11')
        self.add('3', item='MLA999')
        self.add('4', day='03')
        self.add('5', answered_day='03')
        self.add('6', day='02', answer='')
        self.assertEqual([r['id'] for r in conversation(self.store,'6')], ['conversation:1'])

    def test_missing_identity_does_not_merge_buyers(self):
        self.add('1', buyer=None)
        self.add('2', buyer=None, day='02', answer='')
        self.assertEqual(conversation(self.store,'2'), [])

    def test_identity_cannot_change_and_can_be_backfilled(self):
        self.add('1', buyer=None)
        self.add('1')
        with self.assertRaises(ValueError):
            self.add('1', buyer='20')
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT buyer_id FROM questions').fetchone()[0], '10')

    def test_blind_snapshot_removes_target_and_future_answers(self):
        self.add('1')
        self.add('2', day='02', answer='TARGET_SECRET')
        self.add('3', day='03', answer='FUTURE_SECRET')
        # This store is a disposable evaluation copy.
        blind_snapshot(self.store, '2')
        from assistant_knowledge import retrieve
        import json
        context=json.dumps(retrieve(self.store,'2','MLA123','Consulta'))
        self.assertNotIn('TARGET_SECRET', context)
        self.assertNotIn('FUTURE_SECRET', context)
        self.assertEqual([r['id'] for r in conversation(self.store,'2')], ['conversation:1'])

    def test_repeat_ingest_preserves_decisions(self):
        self.add('1', answer='')
        self.store.decide('1','REVISADA','',1)
        self.add('1', answer='')
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0], 1)
