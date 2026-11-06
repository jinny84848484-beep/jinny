import copy,datetime,json,unittest
from unittest.mock import patch
import reviewed_resume as r

NOW=datetime.datetime(2026,11,6,11,tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
class Resume(unittest.TestCase):
 def setUp(self):
  self.record={'status':'reviewed','retry_hold':True,'cached_recovery':{'run_id':r.SOURCE_RUN},'jobs':[],'media':{},'review':json.dumps({'status':'PASS','issues':[],'selection':{}})}
  self.ledger={'days':{r.DAY:self.record},'publications':[]}
 def test_exact_fingerprints_and_forbidden_state(self):
  with patch.object(r.c.p,'now',return_value=NOW),patch.object(r,'RECORD_HASH',r.c.fingerprint(self.record)),patch.object(r,'PUBLICATIONS_HASH',r.c.fingerprint([])),patch.object(r,'validate_staged',return_value=()),patch.object(r.c,'validate_batch'):
   self.assertEqual(r.inspect_ledger(self.ledger),())
   for key,value in [('active_job','x'),('reviewed_resume',{}),('retry_hold',False),('status','tts_submitted')]:
    changed=copy.deepcopy(self.ledger);changed['days'][r.DAY][key]=value
    with self.assertRaises(ValueError):r.inspect_ledger(changed)
   self.ledger['publications'].append({'job_id':'new'})
   with self.assertRaises(ValueError):r.inspect_ledger(self.ledger)
 def execute(self,cas=None,read=None,publish=None):
  with patch.object(r,'verify_context'),patch.object(r,'verify_source'),patch.object(r,'inspect_ledger',return_value=()),patch.object(r.c.p,'now',return_value=NOW),patch.dict(r.os.environ,{'GITHUB_RUN_ID':'999999'}),patch.object(r.c,'journal_get',side_effect=read or [(copy.deepcopy(self.ledger),'a'),(copy.deepcopy(self.ledger),'b')]),patch.object(r.c,'preflight'),patch.object(r.c,'journal_put',side_effect=cas or (lambda l,s:'owned')) as put,patch.object(r.c,'publish_reviewed',side_effect=publish) as pub,patch.object(r.c,'openai',side_effect=AssertionError('No review API')):
   try:r.run_resume()
   finally:self.put=put;self.pub=pub
 def test_cas_before_shared_publish_preserves_history(self):
  self.execute();self.assertEqual(self.put.call_count,1);self.assertEqual(self.pub.call_count,1)
  l,sha=self.put.call_args.args;self.assertEqual(sha,'b');rec=l['days'][r.DAY]
  self.assertTrue(rec['retry_hold']);self.assertEqual(rec['cached_recovery'],self.record['cached_recovery']);self.assertEqual(rec['reviewed_resume']['run_id'],'999999')
 def test_cas_conflict_never_publishes(self):
  with self.assertRaisesRegex(RuntimeError,'stage=ownership'):self.execute(cas=RuntimeError('secret CAS'))
  self.pub.assert_not_called()
 def test_sanitized_download_marker(self):
  def publish(*args,**kwargs):
   kwargs['stage_callback']('download');raise r.c.MediaDownloadError('Media download HTTP 403')
  with self.assertRaisesRegex(RuntimeError,'stage=download Media download HTTP 403'):self.execute(publish=publish)
 def test_unknown_errors_not_exposed(self):
  def publish(*args,**kwargs):
   kwargs['stage_callback']('upload');raise RuntimeError('secret URL')
  with self.assertRaisesRegex(RuntimeError,'^Reviewed resume stopped: stage=upload; reconcile journal, no automatic retry$'):self.execute(publish=publish)
 def test_context_fails_closed(self):
  with patch.dict(r.os.environ,{},clear=True),self.assertRaises(ValueError):r.verify_context()
