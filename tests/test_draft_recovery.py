import copy
import json
import unittest
from unittest.mock import patch
import cloud_runner as c


class DraftRecovery(unittest.TestCase):
    def test_length_failure_records_only_counts_and_preserves_history(self):
        ledger={'days':{'old':{'status':'complete'},'today':{'status':'research_submitted','research_failures':[{'reason':'old'}]}},'publications':[{'video_id':'existing'}]}
        before=copy.deepcopy(ledger)
        jobs=[{'id':'private-id','title':'private-title','channel':'ojirap','scenes':[{'narration':'x'*451}]}]
        with patch.object(c,'validate_batch',side_effect=ValueError('Korean narration length outside Shorts limits')),patch.object(c,'journal_put') as save,patch.object(c,'http') as network:
            with self.assertRaisesRegex(RuntimeError,'korean_narration_length'):
                c.guarded_draft_validation(ledger,'sha','today',json.dumps({'jobs':jobs}),set())
            save.assert_called_once_with(ledger,'sha');network.assert_not_called()
        self.assertEqual(ledger['publications'],before['publications'])
        self.assertEqual(ledger['days']['old'],before['days']['old'])
        self.assertEqual(ledger['days']['today']['research_failures'],before['days']['today']['research_failures'])
        self.assertTrue(ledger['days']['today']['retry_hold'])
        record=ledger['days']['today']['draft_failures'][0]
        self.assertEqual(record['narration_counts'][0]['characters'],451)
        for secret in ['private-id','private-title','x'*451]:self.assertNotIn(secret,json.dumps(record))

    def test_malformed_draft_is_held_without_raw_exception(self):
        for draft in ['secret-invalid-json','{}','{"jobs":null}','{"jobs":[null]}']:
            ledger={'days':{'today':{'status':'research_submitted'}}}
            with patch.object(c,'journal_put'),patch.object(c,'validate_batch',side_effect=TypeError('private-error-detail')):
                with self.assertRaisesRegex(RuntimeError,'draft_json_syntax|missing_jobs|draft_schema_type') as caught:
                    c.guarded_draft_validation(ledger,'sha','today',draft,set())
            self.assertNotIn('private',str(caught.exception))
            self.assertNotIn('secret',json.dumps(ledger))
            self.assertTrue(ledger['days']['today']['retry_hold'])

    def test_valid_draft_uses_existing_validator_without_write(self):
        jobs=[{'channel':'ojirap'}]
        with patch.object(c,'validate_batch') as validate,patch.object(c,'journal_put') as save:
            result=c.guarded_draft_validation({},'sha','today',json.dumps({'jobs':jobs}),{'old'})
            self.assertEqual(result,jobs)
            validate.assert_called_once_with(jobs,'today',{'old'});save.assert_not_called()

    def test_failed_save_does_not_retry_generation(self):
        ledger={'days':{'today':{'status':'research_submitted'}}}
        with patch.object(c,'validate_batch',side_effect=ValueError('failure')),patch.object(c,'journal_put',side_effect=RuntimeError('write failed')) as save,patch.object(c,'http') as network:
            with self.assertRaisesRegex(RuntimeError,'write failed'):c.guarded_draft_validation(ledger,'sha','today','{"jobs":[]}',set())
            save.assert_called_once();network.assert_not_called()

    def test_hold_blocks_all_downstream_work(self):
        day=c.p.now().date().isoformat()
        ledger={'days':{day:{'status':'draft_validation_failed','retry_hold':True}},'publications':[]}
        with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as preflight,patch.object(c,'http') as network:
            with self.assertRaisesRegex(RuntimeError,'hold'):c.produce()
            preflight.assert_not_called();network.assert_not_called()
