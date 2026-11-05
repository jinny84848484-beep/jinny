import copy
import json
import unittest
from unittest.mock import patch
import staged_transfer as s
import cloud_runner as c
from test_guards import batch


class StagedTransfer(unittest.TestCase):
    def setUp(self):self.signer=s.Ed25519PrivateKey.generate()
    def receiver(self):
        return s.create_receiver('jinny84848484-beep/jinny','123','1','2026-11-05',s.public(self.signer))

    def test_roundtrip_validates_without_network_and_plaintext_not_in_envelope(self):
        receiver=self.receiver();data={'jobs':batch()}
        envelope=s.seal_batch(receiver.announcement,data,self.signer)
        self.assertNotIn(b'Test title',envelope)
        self.assertNotIn(b'example.org',envelope)
        with patch.object(c,'http') as network:
            result=receiver.receive(envelope)
            c.validate_batch(result['jobs'],'2026-11-05',set())
            network.assert_not_called()
        self.assertIsNone(receiver._private)
        with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(envelope)

    def test_tamper_consumes_session_without_leaking_content(self):
        for field in ['nonce','ciphertext','sender_key']:
            receiver=self.receiver();envelope=json.loads(s.seal_batch(receiver.announcement,{'jobs':batch()},self.signer))
            envelope[field]='private raw text'
            with self.assertRaisesRegex(ValueError,'^Staged input rejected; session closed$'):
                receiver.receive(s.canonical(envelope))
            self.assertIsNone(receiver._private)

    def test_wrong_run_attempt_day_repository_and_key_are_rejected(self):
        for field in ['run_id','run_attempt','day','repository','receiver_key','session_id']:
            receiver=self.receiver();announcement=copy.deepcopy(receiver.announcement)
            envelope=json.loads(s.seal_batch(announcement,{'jobs':batch()},self.signer))
            envelope['session'][field]='different'
            with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(s.canonical(envelope))

    def test_other_receiver_cannot_decrypt(self):
        first=self.receiver();second=self.receiver()
        envelope=s.seal_batch(first.announcement,{'jobs':batch()},self.signer)
        with self.assertRaisesRegex(ValueError,'session closed'):second.receive(envelope)

    def test_expiry_and_size_fail_closed(self):
        receiver=self.receiver();envelope=s.seal_batch(receiver.announcement,{'jobs':batch()},self.signer)
        with patch.object(s.time,'monotonic',return_value=receiver.deadline+1):
            with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(envelope)
        receiver=self.receiver()
        with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(b'x'*(2*s.MAX_BYTES+1))
        with self.assertRaisesRegex(ValueError,'too large'):s.seal_batch(self.receiver().announcement,{'jobs':['x'*s.MAX_BYTES]},self.signer)

    def test_extra_fields_and_malformed_input_fail_closed(self):
        for value in [b'not-json',b'null',b'[]',b'{}']:
            with self.assertRaisesRegex(ValueError,'session closed'):self.receiver().receive(value)
        receiver=self.receiver();envelope=json.loads(s.seal_batch(receiver.announcement,{'jobs':batch()},self.signer))
        envelope['private']='not allowed'
        with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(s.canonical(envelope))

    def test_encryption_is_not_quality_approval(self):
        receiver=self.receiver();data={'jobs':batch()};data['jobs'][0]['sources']=[]
        result=receiver.receive(s.seal_batch(receiver.announcement,data,self.signer))
        with self.assertRaisesRegex(ValueError,'independent source'):c.validate_batch(result['jobs'],'2026-11-05',set())

    def test_unsigned_or_other_identity_payload_rejected(self):
        receiver=self.receiver();envelope=json.loads(s.seal_batch(receiver.announcement,{'jobs':batch()},self.signer))
        envelope.pop('signature')
        with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(s.canonical(envelope))
        receiver=self.receiver();other=s.Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError,'signing identity'):s.seal_batch(receiver.announcement,{'jobs':batch()},other)
        envelope=json.loads(s.seal_batch(receiver.announcement,{'jobs':batch()},self.signer))
        envelope.pop('signature');envelope['signature']=s.b64(other.sign(s.canonical(envelope)))
        with self.assertRaisesRegex(ValueError,'session closed'):receiver.receive(s.canonical(envelope))
