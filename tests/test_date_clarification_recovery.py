import copy,datetime,hashlib,io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import cloud_runner as c
import date_clarification_recovery as r
from test_date_narration_fix import DateFix
class Recovery(unittest.TestCase):
 def exercise(self,cas_fail=False,review_fail=False,mutation=None):
  saved=DateFix().fixture();record={'status':'editorial_review_failed','retry_hold':True,'checkpoints':{},'review_input_hash':c.fingerprint(saved)}
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:
   for stage in ('research','research_repair','draft','editorial_review_0','editorial_review_1'):
    cp={'day':r.DAY,'stage':stage,'request_hash':stage,'result':{'text':'offline fixture'}};raw=json.dumps(cp).encode();folder='recovery/originals/' if stage.startswith('research') else 'checkpoints/'
    z.writestr(r.DAY+'/'+folder+stage+'.json',raw);record['checkpoints'][stage]={'request_hash':stage,'sha256':hashlib.sha256(raw).hexdigest()}
   z.writestr(r.DAY+'/checkpoints/editorial-review-input.json',json.dumps(saved))
  raw=stream.getvalue();ledger={'days':{r.DAY:copy.deepcopy(record)},'publications':[]};events=[]
  def put(*args):
   events.append('cas')
   if cas_fail:raise RuntimeError('CAS failure')
   return 'newsha'
  def review(l,s,d,j,**kw):
   self.assertTrue(l['days'][d]['retry_hold']);self.assertEqual(kw['correction_namespace'],'date_clarification_v1');events.append('review')
   if review_fail:raise RuntimeError('FAIL stays held')
   return 'reviewsha'
  with patch.dict(os.environ,{'RUN_MODE':'clarify_date_20261115','PUBLIC_UPLOADS_ENABLED':'true'}),tempfile.TemporaryDirectory() as td,patch.object(c.p,'ROOT',Path(td)),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,15,tzinfo=c.p.KST)),patch.object(r,'SIZE',len(raw)),patch.object(r,'SHA',hashlib.sha256(raw).hexdigest()),patch.object(r,'RECORD',c.fingerprint(record)),patch.object(r.fix,'INPUT_HASH',c.fingerprint(saved)),patch.object(c,'journal_get',return_value=(ledger,'oldsha')),patch.object(c,'journal_put',side_effect=put),patch.object(c,'preflight'),patch.object(c,'validate_batch'),patch.object(r.original,'recovered_manifest'),patch.object(c,'review_jobs',side_effect=review),patch.object(c,'publish_reviewed',side_effect=lambda *a,**k:events.append('publish')):
   corrected=r.fix.corrected_request(saved)
   with patch.object(r,'CORRECTED',c.fingerprint(corrected)):
    if mutation:mutation(ledger)
    if cas_fail or review_fail or mutation:
     with self.assertRaises((ValueError,RuntimeError)):r.recover(raw)
     self.assertNotIn('publish',events)
     if cas_fail:self.assertNotIn('review',events)
    else:
     r.recover(raw);self.assertEqual(events,['cas','review','publish']);self.assertEqual(record,ledger['days'][r.DAY]['date_clarification']['previous_record']);self.assertEqual(record['checkpoints'],ledger['days'][r.DAY]['checkpoints'])
     self.assertTrue((Path(td)/'runs'/r.DAY/'checkpoints/editorial_review_0.json').exists())
 def test_cas_then_two_new_review_namespace_then_publication(self):self.exercise()
 def test_cas_failure_never_reviews(self):self.exercise(cas_fail=True)
 def test_review_failure_never_publishes(self):self.exercise(review_fail=True)
 def test_changed_hold_rejected(self):self.exercise(mutation=lambda l:l['days'][r.DAY].update(retry_hold=False))
 def test_duplicate_publication_rejected(self):self.exercise(mutation=lambda l:l['publications'].append({'job_id':r.DAY+'-existing'}))
 def test_scheduled_mode_and_after_event_rejected(self):
  for mode,hour in [('produce',15),('clarify_date_20261115',17)]:
   with patch.dict(os.environ,{'RUN_MODE':mode,'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,hour,tzinfo=c.p.KST)):
    with self.assertRaises(ValueError):r.scope()
class NamespacedReviews(unittest.TestCase):
 def test_both_new_stages_preserve_original_checkpoints_and_review_hash(self):
  import daily_graphics as g
  from test_guards import batch
  from test_daily_graphics import job_of
  jobs=[j for j in batch() if j['channel']=='ojirap'];day='2026-11-05'
  old={'editorial_review_0':{'status':'completed','sha256':'original-fail'},'editorial_review_1':{'status':'completed','sha256':'original-pass'}}
  rec={'date_clarification':{'source_run':r.SOURCE_RUN},'retry_hold':True,'checkpoints':copy.deepcopy(old),'review_input_hash':'original-batch-hash'};ledger={'days':{day:rec},'publications':[]}
  calls=[]
  def provider(prompt,*args,**kw):
   job=job_of(prompt);calls.append(job['id']);return json.dumps({'status':'PASS','issues':[],'selection':{job['id']:[g.media_key(job,i) for i in range(len(job['scenes']))]}}),[]
  with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'RUN_MODE':'clarify_date_20261115'}),patch.object(c.p,'ROOT',Path(td)),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,5,tzinfo=c.p.KST)),patch.object(c,'journal_put',return_value='sha'),patch.object(c,'review_candidates',return_value={}),patch.object(g,'enabled',return_value=True),patch.object(c,'openai',side_effect=provider):
   c.review_jobs(ledger,'sha',day,jobs,correction_namespace='date_clarification_v1')
   self.assertEqual(len(calls),2)
   self.assertEqual(rec['review_input_hash'],'original-batch-hash')
   for k,v in old.items():self.assertEqual(rec['checkpoints'][k],v)
   self.assertTrue(all('editorial_review_'+str(i)+'_date_clarification_v1' in rec['checkpoints'] for i in (0,1)))
   self.assertTrue((Path(td)/'runs'/day/'checkpoints/editorial-review-input-date_clarification_v1.json').exists())
   self.assertFalse((Path(td)/'runs'/day/'checkpoints/editorial-review-input.json').exists())

class ReceiptPersistence(unittest.TestCase):
 def test_cutoff_blocks_new_work_but_not_success_receipts(self):
  with patch.dict(os.environ,{'RUN_MODE':'clarify_date_20261115','PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,17,tzinfo=c.p.KST)):
   for stage in ['publication_marker','completion_marker']:r.publication_scope(stage)
   for stage in ['job_files','tts_marker','render','channel_verification','upload_marker','upload']:
    with self.assertRaises(ValueError):r.publication_scope(stage)
 def test_midnight_allows_existing_receipt_only(self):
  with patch.dict(os.environ,{'RUN_MODE':'clarify_date_20261115','PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,16,0,tzinfo=c.p.KST)):
   r.publication_scope('publication_marker');r.publication_scope('completion_marker')
   with self.assertRaises(ValueError):r.publication_scope('upload')
