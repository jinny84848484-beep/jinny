import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cloud_runner as c
from test_guards import batch
from test_source_manifest import manifest_for,evidence_for

class ResearchRepair(unittest.TestCase):
    def setUp(self):
        self.original=manifest_for(batch());self.fixed=copy.deepcopy(self.original)
        for index in (2,3):
            self.original['stories'][index]['ready']=False
            story=self.fixed['stories'][index];story['topic_key']='replacement-event-'+str(index)
            for source in story['sources']:source['url']+='/replacement-'+str(index)
        self.old_evidence=evidence_for(self.original)
        self.new_evidence=evidence_for({'stories':self.fixed['stories'][2:]})
        self.ledger={'days':{'day':{}},'publications':[]}

    def run_repair(self,provider):
        return c.guarded_research_manifest(self.ledger,'sha','day',json.dumps(self.original),self.old_evidence,set(),allow_repair=True,repair_provider=provider)

    def test_one_repair_merges_actual_evidence_for_retained_and_replaced_stories(self):
        provider=Mock(return_value=(json.dumps(self.fixed),self.new_evidence))
        self.assertEqual(self.run_repair(provider),self.fixed)
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['stage'],'research_repair')
        self.assertIn('unique-event-2',provider.call_args.args[0])
        self.assertIn('Do not reuse any excluded unready event',provider.call_args.args[0])

    def test_still_unready_holds_after_one_repair_without_a_loop(self):
        provider=Mock(return_value=(json.dumps(self.original),self.old_evidence))
        with patch.object(c,'journal_put') as save,patch.object(c,'openai') as api:
            with self.assertRaisesRegex(RuntimeError,'research_manifest_unready'):self.run_repair(provider)
            provider.assert_called_once();save.assert_called_once();api.assert_not_called()
        self.assertTrue(self.ledger['days']['day']['retry_hold'])

    def test_ready_label_cannot_create_missing_actual_source_evidence(self):
        provider=Mock(return_value=(json.dumps(self.fixed),[]))
        with patch.object(c,'journal_put'):
            with self.assertRaisesRegex(RuntimeError,'evidence_missing'):self.run_repair(provider)
        provider.assert_called_once()

    def test_unconfirmed_excluded_event_cannot_be_promoted_by_flipping_ready(self):
        promoted=copy.deepcopy(self.original)
        for story in promoted['stories']:story['ready']=True
        with patch.object(c,'journal_put'):
            with self.assertRaisesRegex(RuntimeError,'reused_unready'):self.run_repair(Mock(return_value=(json.dumps(promoted),self.old_evidence)))

    def test_checkpoint_reuses_completed_repair_and_does_not_repeat_paid_request(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put',return_value='next'):
            actual=Mock(return_value=(json.dumps(self.fixed),self.new_evidence))
            def provider(*args,**kwargs):
                result,_=c.stage_checkpoint(self.ledger,'sha','day','research_repair',request={'args':args,'kwargs':kwargs},submit=actual)
                return result
            self.assertEqual(self.run_repair(provider),self.fixed)
            self.assertEqual(self.run_repair(provider),self.fixed)
            actual.assert_called_once()
            self.assertEqual(self.ledger['days']['day']['checkpoints']['research_repair']['attempts'],1)

    def test_network_error_does_not_trigger_repair_or_mark_completed_validation_failure(self):
        provider=Mock(side_effect=RuntimeError('Network result uncertain'))
        with patch.object(c,'journal_put') as save:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):self.run_repair(provider)
            provider.assert_called_once();save.assert_not_called()
        self.assertNotIn('retry_hold',self.ledger['days']['day'])

    def test_ready_original_manifest_never_calls_repair(self):
        provider=Mock()
        result=c.guarded_research_manifest(self.ledger,'sha','day',json.dumps(self.fixed),[*self.old_evidence,*self.new_evidence],set(),allow_repair=True,repair_provider=provider)
        self.assertEqual(result,self.fixed);provider.assert_not_called()
