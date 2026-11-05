import copy,json,unittest
from unittest.mock import patch
import cloud_runner as c

class NarrationCorrection(unittest.TestCase):
    def test_only_narration_is_changed(self):
        jobs=[{'id':'original','channel':'ojirap','sources':[{'url':'https://example.org'}],'scenes':[{'narration':'old','source_indices':[0],'headline':'keep'}]}]
        before=copy.deepcopy(jobs)
        with patch.object(c,'openai',return_value=(json.dumps({'narrations':[['new']]}),[])) as api:
            corrected=c.correct_narration_lengths_once(jobs)
            api.assert_called_once()
            self.assertIn('Return JSON',api.call_args.args[0])
            self.assertFalse(api.call_args.args[1]);self.assertEqual(api.call_args.args[2],3500)
        self.assertEqual(jobs,before)
        expected=copy.deepcopy(before);expected[0]['scenes'][0]['narration']='new'
        self.assertEqual(corrected,expected)

    def test_invalid_correction_cannot_replace_metadata(self):
        jobs=[{'scenes':[{'narration':'old'}]}]
        for result in [{'jobs':[]},{'narrations':[]},{'narrations':[['new']],'sources':[]},{'narrations':[[1]]}]:
            with patch.object(c,'openai',return_value=(json.dumps(result),[])):
                with self.assertRaises(ValueError):c.correct_narration_lengths_once(jobs)

    def test_one_correction_then_all_guards_rerun(self):
        jobs=[{'channel':'ojirap','scenes':[{'narration':'old'}]}]
        with patch.object(c,'validate_batch',side_effect=[ValueError('Korean narration length outside Shorts limits'),None]) as validate,patch.object(c,'correct_narration_lengths_once',return_value=jobs) as correction,patch.object(c,'journal_put') as save:
            self.assertEqual(c.guarded_draft_validation({},'sha','day',json.dumps({'jobs':jobs}),set(),allow_length_correction=True),jobs)
            self.assertEqual(validate.call_count,2);correction.assert_called_once();save.assert_not_called()

    def test_second_length_failure_is_held_without_loop(self):
        ledger={'days':{'day':{'status':'research_submitted'}},'publications':[]}
        jobs=[{'channel':'ojirap','scenes':[{'narration':'old'}]}]
        with patch.object(c,'validate_batch',side_effect=ValueError('Korean narration length outside Shorts limits')) as validate,patch.object(c,'correct_narration_lengths_once',return_value=jobs) as correction,patch.object(c,'journal_put'):
            with self.assertRaisesRegex(RuntimeError,'korean_narration_length'):c.guarded_draft_validation(ledger,'sha','day',json.dumps({'jobs':jobs}),set(),allow_length_correction=True)
            self.assertEqual(validate.call_count,2);correction.assert_called_once()
        self.assertTrue(ledger['days']['day']['retry_hold'])

    def test_source_failure_never_calls_correction(self):
        ledger={'days':{'day':{'status':'research_submitted'}}}
        with patch.object(c,'validate_batch',side_effect=ValueError('Two independent source domains required')),patch.object(c,'correct_narration_lengths_once') as correction,patch.object(c,'journal_put'):
            with self.assertRaises(RuntimeError):c.guarded_draft_validation(ledger,'sha','day','{"jobs":[]}',set(),allow_length_correction=True)
            correction.assert_not_called()

    def test_network_failure_preserves_pending_marker(self):
        ledger={'days':{'day':{'status':'research_submitted'}}}
        with patch.object(c,'validate_batch',side_effect=ValueError('Korean narration length outside Shorts limits')),patch.object(c,'correct_narration_lengths_once',side_effect=RuntimeError('network failed')),patch.object(c,'journal_put') as save:
            with self.assertRaisesRegex(RuntimeError,'network failed'):c.guarded_draft_validation(ledger,'sha','day','{"jobs":[]}',set(),allow_length_correction=True)
            save.assert_not_called()
        self.assertEqual(ledger['days']['day']['status'],'research_submitted')
