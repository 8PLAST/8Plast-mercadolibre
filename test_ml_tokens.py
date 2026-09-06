import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from ml_integration import MercadoLibreClient


class TokenTests(unittest.TestCase):
    def test_atomic_replacement_preserves_old_file_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'secure.dat'
            path.write_bytes(b'previous')
            with patch('ml_integration.SECURE_CONFIG', path), patch('ml_integration.protect', lambda b:b):
                client = MercadoLibreClient(None)
                with patch('ml_integration.os.replace', side_effect=OSError('simulated failure')):
                    with self.assertRaises(OSError): client._write_secure({'access_token':'test'})
                self.assertEqual(path.read_bytes(), b'previous')
                self.assertEqual(len(list(Path(folder).iterdir())), 1)
                client._save_tokens({'refresh_token':'old'}, {'access_token':'new', 'refresh_token':'rotated','expires_in':21600})
                result = json.loads(base64.b64decode(path.read_bytes()))
                self.assertEqual(result['access_token'], 'new')
                self.assertEqual(result['refresh_token'], 'rotated')
                self.assertGreater(result['expires_at'], time.time())

    def test_rejected_token_refreshes_but_concurrent_replacement_is_reused(self):
        client = MercadoLibreClient(None)
        secure = {'access_token':'old','refresh_token':'refresh','client_secret':'secret','expires_at':time.time()+5000}
        def save(existing,payload): existing.update(payload)
        with patch.object(client,'secure_config',return_value=secure), patch.object(client,'public_config',return_value={'client_id':'test'}), patch.object(client,'_save_tokens',side_effect=save), patch.object(client,'_request',return_value={'access_token':'new','refresh_token':'rotated'}) as request:
            self.assertEqual(client._access_token_locked('old'),'new')
            self.assertEqual(client._access_token_locked('old'),'new')
            self.assertEqual(request.call_count,1)
