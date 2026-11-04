import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant_center import ReviewStore
from assistant_automation import initialize, ServiceFailure
from assistant_diagnostics import probe, status


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ReviewStore(Path(self.tmp.name)/'review.db');initialize(self.store)

    def test_probe_recovers_quota_block_without_publishing(self):
        with self.store.connect() as con:
            con.execute("UPDATE assistant_ai_control SET blocked_error='AI_QUOTA'")
        with patch('assistant_automation.ai_draft',return_value=('Conectado',{},[],{})) as ai, patch('assistant_publishing.publish',side_effect=AssertionError('No publication')):
            self.assertEqual(probe(self.store)['state'],'OK')
            probe(self.store)
            self.assertEqual(ai.call_count,1)
        with self.store.connect() as con:
            self.assertIsNone(con.execute('SELECT blocked_error FROM assistant_ai_control').fetchone()[0])
            self.assertEqual(con.execute('SELECT count(*) FROM questions').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM assistant_alerts').fetchone()[0],0)

    def test_failed_probe_does_not_clear_block(self):
        with self.store.connect() as con:con.execute("UPDATE assistant_ai_control SET blocked_error='AI_QUOTA'")
        with patch('assistant_automation.ai_draft',side_effect=ServiceFailure('AI_QUOTA')):
            self.assertEqual(probe(self.store)['error'],'AI_QUOTA')
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT blocked_error FROM assistant_ai_control').fetchone()[0],'AI_QUOTA')
        self.assertFalse(status(self.store)['automatic_publishing'])
