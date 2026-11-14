import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
import cloud_runner as c
import editorial_policy_recheck as r
from test_guards import batch

DAY='2026-11-05'

class EditorialRecheck(unittest.TestCase):
    def test_wrong_archive_never_calls_paid_or_publication(self):
        with patch.object(c,'openai') as api,patch.object(c,'publish_reviewed') as publish,patch.object(c,'journal_put') as save:
            with self.assertRaises(ValueError):r.recover_from_archive(b'wrong')
            for call in (api,publish,save):call.assert_not_called()

    def test_scope_clarification_keeps_real_approval_requirement(self):
        self.assertIn('Return approval_required',r.CLARIFICATION)
        self.assertIn('not an instruction to PASS',r.CLARIFICATION)
        self.assertIn('sensitive personal disclosure',r.CLARIFICATION)

    def exercise(self,second_pass):
        jobs=[j for j in batch() if j['channel']=='ojirap'];media={}
        for j in jobs:
            for suffix in ('a','b'):
                media[j['id']+suffix]={'topic_key':j['topic_key'],'channel':'ojirap','relevance_verified':False}
        count=[]
        def provider(prompt,*args,**kwargs):
            job=json.loads(prompt.split('\nJOB:\n')[1].split('\nIMAGE METADATA:\n')[0]);count.append(kwargs['instructions'])
            success=job['id']==jobs[0]['id'] or (second_pass and r.CLARIFICATION in kwargs['instructions'])
            decision=({'status':'PASS','issues':[],'selection':{job['id']:[job['id']+'a',job['id']+'b']*2}} if success else {'status':'FAIL','issues':[{'code':'approval_required','job_index':0,'scene_index':-1,'explanation':'unresolved approval issue'}],'selection':{}})
            return json.dumps(decision),[{'url':'https://example.org/report'}]
        ledger={'days':{DAY:{}},'publications':[]}
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put',return_value='sha'),patch.object(c,'review_candidates',return_value=media),patch.object(c,'openai',side_effect=provider) as api,patch.object(c,'publish_reviewed') as publish,redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'batch held'):c.review_jobs(ledger,'sha',DAY,jobs)
            before=copy.deepcopy(ledger['days'][DAY]['checkpoints'])
            original=(Path(directory)/'runs'/DAY/'checkpoints/editorial_review_1.json').read_bytes()
            if second_pass:c.review_jobs(ledger,'sha',DAY,jobs,policy_recheck=True)
            else:
                with self.assertRaisesRegex(RuntimeError,'batch held'):c.review_jobs(ledger,'sha',DAY,jobs,policy_recheck=True)
            self.assertEqual(api.call_count,3)  # Two original reviews; ONE additional review.
            self.assertEqual(ledger['days'][DAY]['checkpoints']['editorial_review_1'],before['editorial_review_1'])
            self.assertEqual((Path(directory)/'runs'/DAY/'checkpoints/editorial_review_1.json').read_bytes(),original)
            self.assertEqual(ledger['days'][DAY]['retry_hold'],not second_pass)
            self.assertIn(c.DAILY_PUBLICATION_SCOPE,count[-1])
            self.assertIn(r.CLARIFICATION,count[-1]);self.assertNotIn(r.CLARIFICATION,count[0])
            publish.assert_not_called()

    def test_failed_recheck_remains_held_with_original_decision_preserved(self):self.exercise(False)
    def test_only_real_new_pass_allows_reviewed_state(self):self.exercise(True)
