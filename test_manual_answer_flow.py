import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant_center import ReviewStore
from assistant_preview import create_app
from assistant_knowledge import retrieve
from assistant_manual_learning import comparison,reconcile
from assistant_publishing import publish
import assistant_automation as auto
from technical_catalog import link,load


def incoming(qid='1',answer=None):
    q={'id':qid,'item_id':'MLA123','text':'¿De cuántos micrones son?','status':'ANSWERED' if answer is not None else 'UNANSWERED','date_created':'2026-11-03T10:00:00+00:00'}
    if answer is not None:q['answer']={'text':answer,'date_created':'2026-11-03T10:01:00+00:00'}
    return {'questions':[q],'publications':{'MLA123':{'title':'Bolsa roja 90x120','attributes':[{'id':'THICKNESS','value_name':'100 micrones'}]}}}


class ManualAnswerFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'reviews.db';self.store=ReviewStore(self.path);auto.initialize(self.store)
        with self.store.connect() as con:con.execute('UPDATE assistant_monitor SET enabled=1 WHERE id=1')
        link(self.store,'MLA123','red_pathological-90x120-100-p100',{},'fixture owner validation')

    def generate(self,qid='1',text='¡Hola! Son de 100 micrones reales por cara.'):
        with patch.object(auto,'ai_draft',return_value=(text,{},[])):
            auto.draft_question(self.store,qid)

    def q(self,qid='1'):
        with self.store.connect() as con:return dict(con.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone())

    def observations(self):
        with self.store.connect() as con:return [dict(r) for r in con.execute('SELECT * FROM assistant_response_observations')]

    def test_new_question_automatically_generates_in_existing_tab_without_editor(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(auto,'fetch_questions',return_value=(incoming(),[])),patch.object(auto,'check_pairing'),patch.object(auto,'dispatch'),patch.object(auto,'ai_draft',return_value=('Sugerencia automática de prueba',{},[])):
            auto.tick(self.store,lambda:'test')
        page=create_app(self.path).test_client().get('/asistentes').get_data(as_text=True)
        self.assertIn('Sugerencia automática de prueba',page)
        self.assertIn('Aprobar y publicar sugerencia',page)
        self.assertNotIn('Guardar edición',page)
        self.assertNotIn('name="text"',page)
        self.assertNotIn('tu corrección',page)

    def test_manual_answer_is_exact_compared_deduplicated_and_preserves_suggestion(self):
        self.store.ingest(incoming(),'fixture');self.generate();before=self.q()
        answer='¡Hola! Son de 100 micrones REALES por cara. Tienen excelente resistencia.'
        self.store.ingest(incoming(answer=answer),'API oficial')
        self.store.ingest(incoming(answer=answer),'API oficial repetida')
        obs=self.observations();self.assertEqual(len(obs),1)
        self.assertEqual(obs[0]['answer_text'],answer);self.assertEqual(obs[0]['suggested_text'],before['original'])
        delta=json.loads(obs[0]['comparison']);self.assertTrue(delta['has_suggestion']);self.assertTrue(delta['added'])
        self.assertEqual(self.q()['original'],before['original']);self.assertEqual(self.q()['status'],'RESPONDIDA_MANUALMENTE')
        self.assertGreater(self.q()['version'],before['version'])
        page=create_app(self.path).test_client().get('/asistentes').get_data(as_text=True)
        self.assertIn('Respondida manualmente en Mercado Libre',page)
        self.assertIn('Respuesta detectada en Mercado Libre y comparación',page)

    def test_new_suggestion_receives_prior_manual_answer_as_example_not_global_rule(self):
        self.store.ingest(incoming(),'fixture');self.generate();manual='¡Hola! El espesor es de 100 micrones reales por cara.'
        self.store.ingest(incoming(answer=manual),'API oficial');self.store.ingest(incoming('2'),'fixture')
        def generation(question,facts,context):
            examples=context['examples'];self.assertEqual(examples[0]['source'],'MANUAL_MERCADO_LIBRE')
            self.assertEqual(examples[0]['answer'],manual)
            self.assertEqual(examples[0]['manual_comparison']['learning_scope'],'example_only')
            self.assertEqual(context['knowledge'][0]['authority'],'owner_catalog')
            return '¡Hola! El espesor es de 100 micrones reales por cara.',{},[]
        with patch.object(auto,'ai_draft',side_effect=generation):auto.draft_question(self.store,'2')
        self.assertEqual(self.q('2')['original'],manual)
        self.assertEqual(load()['families'][2]['variants'][-1]['real_microns_per_face'],100)
        with self.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM assistant_knowledge').fetchone()[0],0)

    def test_incorrect_manual_80_does_not_override_catalog_100(self):
        self.store.ingest(incoming(),'fixture');self.generate()
        self.store.ingest(incoming(answer='Son de 80 micrones reales'),'API oficial')
        context=retrieve(self.store,'2','MLA123','micrones')
        self.assertEqual(context['examples'][0]['authority'],'potentially_outdated_technical_reference')
        self.assertEqual(context['knowledge'][0]['value']['real_microns_per_face'],100)

    def test_old_question_outside_recent_window_is_reconciled_by_id(self):
        self.store.ingest(incoming(),'fixture');self.generate()
        response={**incoming(answer='Respuesta manual antigua')['questions'][0],'seller_id':9}
        def get(path):return {'id':9} if path=='/users/me' else response
        with patch.object(auto.ReadOnlyMercadoLibre,'request',side_effect=get) as api:
            auto.sync_known_answers(self.store,lambda:'test',seen_ids={'999'})
        self.assertEqual(self.q()['external_answer'],'Respuesta manual antigua')
        self.assertIn('/questions/1',[call.args[0] for call in api.call_args_list])

    def test_approve_uses_saved_suggestion_ignores_submitted_text(self):
        self.store.ingest(incoming(),'fixture');self.generate()
        app=create_app(self.path);app.testing=True;client=app.test_client();client.get('/asistentes')
        with client.session_transaction() as session:csrf=session['csrf']
        with patch('assistant_publishing.publish',return_value='PUBLISHED') as publication:
            response=client.post('/asistentes/publicar/1',data={'csrf':csrf,'version':self.q()['version'],'text':'Texto alterado en el navegador'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(publication.call_args.args[2],self.q()['original'])
        client.post('/asistentes/decision/1',data={'csrf':csrf,'version':self.q()['version'],'action':'EDITADA','text':'No permitido'})
        self.assertIsNone(self.q()['edited'])

    def test_manual_response_blocks_stale_approval_without_post(self):
        self.store.ingest(incoming(),'fixture');self.generate();version=self.q()['version']
        answered={**incoming(answer='Ya respondí manualmente')['questions'][0],'seller_id':9}
        def get(path):return {'id':9} if path=='/users/me' else answered
        with patch.dict(os.environ,{'ASSISTANT_PUBLISH_ENABLED':'1'}),patch('assistant_publishing.ReadOnlyMercadoLibre.request',side_effect=get),patch('urllib.request.build_opener') as network:
            with self.assertRaisesRegex(ValueError,'ya fue respondida'):publish(self.store,'1',self.q()['original'],version,lambda:'test')
            network.assert_not_called()
        self.assertEqual(self.q()['external_answer'],'Ya respondí manualmente')
        self.assertEqual(len(self.observations()),1)

    def test_generation_race_preserves_manual_answer(self):
        self.store.ingest(incoming(),'fixture')
        def generating(*args):
            self.store.ingest(incoming(answer='Respuesta manual durante generación'),'API oficial')
            return 'Sugerencia tardía',{},[]
        with patch.object(auto,'ai_draft',side_effect=generating):auto.draft_question(self.store,'1')
        self.assertNotEqual(self.q()['original'],'Sugerencia tardía')
        self.assertEqual(self.q()['external_answer'],'Respuesta manual durante generación')

    def test_comparison_records_omissions_not_claims_of_truth(self):
        diff=comparison('Hola trae 100 bolsas y envío gratis','Hola trae 100 bolsas')
        self.assertIn('y envío gratis',diff['omitted']);self.assertEqual(diff['learning_scope'],'example_only')
        self.assertIn('precio_condiciones',diff['review_topics'])


class HistoricalBackfillTests(unittest.TestCase):
    def test_old_stored_answer_backfilled_without_requesting_recent_window(self):
        with tempfile.TemporaryDirectory() as t:
            store=ReviewStore(Path(t)/'old.db')
            with store.connect() as con:
                con.execute("INSERT INTO questions(id,item_id,title,question,external_status,external_answer,facts,original,missing,used,updated) VALUES('1','MLA123','Bolsa','Consulta','ANSWERED','Respuesta antigua exacta','{}','Fallback','[]','{}','2026-01-01')")
            auto.initialize(store);auto.initialize(store)
            with store.connect() as con:
                observations=con.execute('SELECT * FROM assistant_response_observations').fetchall()
            self.assertEqual(len(observations),1)
            self.assertEqual(observations[0]['answer_text'],'Respuesta antigua exacta')
            self.assertFalse(json.loads(observations[0]['comparison'])['has_suggestion'])

if __name__=='__main__':unittest.main()
