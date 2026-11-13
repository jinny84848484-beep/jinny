import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import corgi_preparation as p

class Preparation(unittest.TestCase):
    def approved(self):
        d=p.plan('Original corgi snoozes');d['budget_credits']=20
        d['cost_approval']={'decision':'approve_generation','request_sha256':p.fingerprint(d['request']),'max_credits':20,'owner_reference':'synthetic mock approval'}
        return d
    def account(self):return {'is_active':True,'plan_id':'PP0001','user_credit':{'available_credit':20}}
    def test_default_zero_blocks_with_key(self):
        with self.assertRaisesRegex(ValueError,'ZERO_BUDGET'):p.submit_live(p.plan('Corgi'),'dummy')
    def test_live_always_disabled(self):
        with self.assertRaisesRegex(RuntimeError,'LIVE_ADAPTER_DISABLED'):p.submit_live(self.approved(),'dummy')
    def test_approval_binds_options(self):
        d=self.approved();d['request']['duration']=4
        with self.assertRaisesRegex(ValueError,'COST_APPROVAL'):p.authorize(d,'dummy')
    def test_official_multipart_schema(self):
        spec=p.request_spec(p.plan('Corgi'))
        self.assertEqual(spec['url'],p.API_BASE+'/video-gen/veo')
        self.assertIn(b'name="duration"\r\n\r\n8',spec['body'])
        self.assertTrue(spec['content_type'].startswith('multipart/form-data; boundary='))
    def test_receipt_precedes_post_and_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'receipt.json'
            def fake(spec):
                self.assertEqual(json.loads(path.read_text())['status'],'submitted_unknown')
                raise TimeoutError('ambiguous')
            with self.assertRaises(TimeoutError):p.mock_submission(self.approved(),'dummy',self.account(),path,fake)
            transport=Mock()
            with self.assertRaises(FileExistsError):p.mock_submission(self.approved(),'dummy',self.account(),path,transport)
            transport.assert_not_called()
    def test_free_account_blocks_transport(self):
        transport=Mock()
        with self.assertRaisesRegex(ValueError,'PREMIUM'):p.mock_submission(self.approved(),'dummy',{'plan_id':'FP0001'},'/unused',transport)
        transport.assert_not_called()
    def test_success_receipt_uuid_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=p.mock_submission(self.approved(),'dummy',self.account(),Path(tmp)/'receipt',lambda _: {'uuid':'c558a44c-c91c-11f0-98b4-0242ac120004','private':'not saved'})
            self.assertEqual(value['status'],'accepted_mock');self.assertNotIn('private',value)
    def test_provider_success_never_approves_publish(self):
        self.assertEqual(p.assess_history({'status':2,'generated_video':[{'video_url':'not-downloaded'}]})['public_approval'],'pending')
        self.assertEqual(p.assess_history({'status':3})['status'],'not_ready')
    def test_technical_check_never_approves_content(self):
        report=p.technical_report({'streams':[{'codec_type':'video','width':720,'height':1280}],'format':{'duration':'8'}})
        self.assertTrue(report['technical_probe_pass']);self.assertFalse(report['publish_ready'])
        self.assertEqual(report['rights_review'],'pending')
