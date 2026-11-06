import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import additional_transport as at
import staged_transfer as t
import additional_driver as d


class PrivateTransportTests(unittest.TestCase):
    def test_single_use_authenticated_wrapper_roundtrip(self):
        signing = Ed25519PrivateKey.generate()
        receiver = t.create_receiver('repo', '12', '1', '2026-11-06', t.public(signing))
        payload = at.wrap_payload({'private': 'script'}, {'private': 'approval'})
        sealed = t.seal_batch(receiver.announcement, payload, signing)
        self.assertNotIn(b'script', sealed)
        unwrapped = at.unwrap_payload(receiver.receive(sealed))
        self.assertEqual(unwrapped['packet'], {'private': 'script'})
        with self.assertRaises(ValueError): receiver.receive(sealed)

    def test_recovery_key_rejects_null_exchange_before_work(self):
        with self.assertRaises(ValueError): at.validate_recovery_key(t.b64(bytes(32)))
        at.validate_recovery_key(t.public(X25519PrivateKey.generate()))

    def test_decrypted_archive_is_private_and_no_overwrite(self):
        import os
        import additional_sender as sender
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'recovery.zip'
            sender.write_private_file(path, b'private')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): sender.write_private_file(path, b'replaced')
            self.assertEqual(path.read_bytes(), b'private')

    def test_wrong_shape_or_version_rejected(self):
        for value in ({'jobs': []}, {'jobs': [{}, {}]}, {'jobs': [{'additional_version': 2}]}, {'packet': {}}):
            with self.assertRaises(ValueError): at.unwrap_payload(value)

    def test_recovery_only_recipient_and_context_can_decrypt(self):
        recipient = X25519PrivateKey.generate(); context = {'run_id': '12', 'batch_id': '2026-11-06-extra-01'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'run'; root.mkdir(); (root / 'private.json').write_text('private source and provider text')
            destination = Path(temp) / 'encrypted.json'
            at.encrypt_recovery(root, destination, t.public(recipient), context)
            raw = destination.read_bytes(); self.assertNotIn(b'private source', raw)
            archive = at.decrypt_recovery(raw, recipient, context)
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                self.assertEqual(z.namelist(), ['private.json'])
                self.assertEqual(z.read('private.json'), b'private source and provider text')
            with self.assertRaises(Exception): at.decrypt_recovery(raw, X25519PrivateKey.generate(), context)
            with self.assertRaises(Exception): at.decrypt_recovery(raw, recipient, {**context, 'run_id': 'other'})
            damaged = json.loads(raw); damaged['ciphertext'] = damaged['ciphertext'][:-8] + 'AAAAAAAA'
            with self.assertRaises(Exception): at.decrypt_recovery(json.dumps(damaged).encode(), recipient, context)

    def test_recovery_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'run'; root.mkdir(); (root / 'link').symlink_to('/does-not-exist')
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                at.encrypt_recovery(root, Path(temp) / 'out', t.public(X25519PrivateKey.generate()), {})

    def test_session_registration_preserves_daily_record(self):
        ledger = {'days': {'2026-11-06': {'status': 'complete', 'retry_hold': True}}, 'publications': []}
        original = copy.deepcopy(ledger['days'])
        d.register_session(ledger, '12', {'run_id': '12'}, '2026-11-06', '2026-11-06-extra-01', 'a' * 64)
        self.assertEqual(ledger['days'], original)
        with self.assertRaises(ValueError):
            d.register_session(ledger, '13', {'run_id': '13'}, '2026-11-06', '2026-11-06-extra-02', 'b' * 64)

    def test_workflow_is_manual_serial_and_only_encrypted_artifacts(self):
        source = (Path(__file__).parents[1] / '.github/workflows/additional-short.yml').read_text()
        self.assertIn('workflow_dispatch:', source); self.assertNotIn('schedule:', source)
        self.assertIn('group: shorts-production', source); self.assertIn('cancel-in-progress: false', source)
        self.assertIn('path: private-artifacts/*.enc.json', source); self.assertNotIn('path: runs/', source)
        self.assertIn('retention-days: 1', source)
        self.assertNotIn('YOUTUBE_KOREAN_OAUTH', source)

if __name__ == '__main__': unittest.main()
