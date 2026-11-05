import json
import unittest
from unittest.mock import patch
import os

from assistant_reasoning import reasoning_context
from technical_catalog import load


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.alternatives = [
            {'id': 'catalog:'+v['id'],
             'value': {**v, 'family': f['id'], 'kind': f['kind']}}
            for f in load()['families'] for v in f['variants']]

    def test_customer_rectangular_bin(self):
        result = reasoning_context(
            'Tengo un tacho de basura de boca rectangular de 40x38 cm y de 57cm de alto: que tamaño de bolsa necesito?',
            self.alternatives, {})[0]['value']
        self.assertEqual(result['minimum_flat_width_cm'], 78)
        self.assertEqual(result['estimated_length_cm'], 91)
        self.assertEqual(result['recommended']['width_cm'], 90)
        self.assertEqual(result['recommended']['length_cm'], 120)
        self.assertTrue(any(x['width_cm']==80 and x['fit']=='ajustado' for x in result['options']))

    def test_missing_or_ambiguous_dimensions_are_not_invented(self):
        for question in ('Tacho de 57 cm de alto', 'Tacho circular de 40x38 cm y 57cm de alto',
                         'Bolsa de 40x38 cm y 57cm de alto'):
            self.assertEqual(reasoning_context(question, self.alternatives, {}), [])

    def test_known_family_not_replaced_with_another_color(self):
        technical = {'identity_confirmed': True, 'knowledge': [{'value': {'family': 'consorcio_green'}}]}
        result = reasoning_context('Tacho de boca rectangular de 40x38 cm y 57cm de alto', self.alternatives, technical)
        self.assertTrue(all('consorcio_green' in option['evidence_id'] for option in result[0]['value']['options']))


class RetrievalTests(unittest.TestCase):
    def test_unlinked_publication_still_has_confirmed_alternatives(self):
        import tempfile
        from pathlib import Path
        from assistant_center import ReviewStore
        from assistant_knowledge import retrieve
        import assistant_automation as auto
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewStore(Path(tmp)/'review.db')
            auto.initialize(store)
            context = retrieve(store, 'new', 'MLA123', 'Qué medida de bolsa necesito?')
            self.assertFalse(context['catalog']['identity_confirmed'])
            self.assertEqual(len(context['catalog_alternatives']), 68)
            self.assertTrue(all(e['authority']=='owner_catalog_alternative' for e in context['catalog_alternatives']))

    def test_generated_answer_can_cite_alternative_and_calculation(self):
        import assistant_automation as auto
        ids = ['catalog:consorcio_black-90x120-40', 'calculation:rectangular_bin']
        response = {'status':'completed', 'output':[{'type':'message', 'content':[{
            'type':'output_text', 'text':json.dumps({'answer':'Recomendamos 90x120 por el ancho de boca.',
                                                 'evidence_ids':ids, 'warnings':[]})}]}]}
        context = {'knowledge':[], 'examples':[],
                   'catalog_alternatives':[{'id':ids[0]}], 'reasoning':[{'id':ids[1]}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test'}), patch.object(auto, 'post_json', return_value=response):
            answer, _, warnings, meta = auto.ai_draft('Consulta', {}, context)
        self.assertIn('90x120', answer)
        self.assertEqual(warnings, [])
        self.assertEqual(meta['evidence_ids'], ids)
