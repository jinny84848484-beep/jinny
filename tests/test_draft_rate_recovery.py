import copy,json,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import draft_rate_recovery_20261117 as r
class Recovery(unittest.TestCase):
 def test_bad_archive_never_reads_state(self):
  with self.assertRaisesRegex(ValueError,'archive'):r.verify_inputs(b'bad',{})
 def test_wrong_day_or_branch_blocks(self):
  with patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/no'}),patch.object(r.c,'journal_get') as read:
   with self.assertRaises(ValueError):r.recover(b'bad')
   read.assert_not_called()
 def test_retry_preserves_history_and_failure_blocks_downstream(self):
  original={'status':'draft_submitted','checkpoints':{'draft':{'status':'submitted'}}}
  ledger={'days':{r.DAY:copy.deepcopy(original)}};writes=[]
  request={'prompt':'x','instructions':'y'}
  with tempfile.TemporaryDirectory() as d,patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/main','PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(r.c.p,'ROOT',Path(d)),patch.object(r.c.p,'now') as now,patch.object(r.c,'journal_get',return_value=(ledger,'sha')),patch.object(r,'verify_inputs',return_value=(request,{},{})),patch.object(r.c,'preflight'),patch.object(r.c,'journal_put',side_effect=lambda l,s:writes.append(copy.deepcopy(l)) or 'next'),patch.object(r.c,'openai',side_effect=RuntimeError('HTTP429')) as api,patch.object(r.c,'review_jobs') as review,patch.object(r.c,'publish_reviewed') as publish:
   now.return_value.date.return_value.isoformat.return_value=r.DAY;now.return_value.isoformat.return_value='test'
   with self.assertRaisesRegex(RuntimeError,'HTTP429'):r.recover(b'fixture')
   self.assertEqual(api.call_count,1);review.assert_not_called();publish.assert_not_called()
   rec=ledger['days'][r.DAY]
   self.assertEqual(rec['draft_429_recovery']['previous_record'],original)
   self.assertEqual(rec['checkpoints']['draft']['status'],'submitted')
   self.assertEqual(len(writes),2)
