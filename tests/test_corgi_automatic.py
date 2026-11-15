import datetime as dt,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import corgi_automatic as m
class Automatic(unittest.TestCase):
 def exercise(self,items,ledger=None,mode='auto_schedule',invalid=False,failure=False):
  ledger=ledger if ledger is not None else {'corgi_delivery':{'mode':m.p.MODE},'corgi_publications':{}}
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'corgi').mkdir();(root/'corgi/queue.json').write_text(json.dumps({'schema_version':1,'items':items}))
   with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':mode}),patch.object(m.legacy,'ROOT',root),patch.object(m.a.c,'journal_get',return_value=(ledger,'sha')),patch.object(m.p,'plan',side_effect=ValueError('unapproved') if invalid else None),patch.object(m.a,'schedule',side_effect=RuntimeError('response lost') if failure else None) as schedule,patch.object(m.a,'reconcile') as reconcile:
    clock=lambda:dt.datetime(2026,11,15,16,30,tzinfo=m.p.KST)
    if failure:
     with self.assertRaises(RuntimeError):m.tick(clock)
     self.assertEqual(schedule.call_count,1);return
    result=m.tick(clock)
    return result,[x.args[0] for x in schedule.call_args_list],[x.args[0] for x in reconcile.call_args_list]
 def test_empty_queue_no_upload(self):self.assertEqual(self.exercise([])[1],[])
 def test_inactive_no_upload(self):self.assertEqual(self.exercise([],ledger={})[0]['state'],'inactive')
 def test_unapproved_no_upload(self):self.assertEqual(self.exercise([{'publish_date_kst':'2026-11-16'}],invalid=True)[1],[])
 def test_queue_dates_not_runtime_date_and_late_never_rolled(self):
  result,days,_=self.exercise([{'publish_date_kst':'2026-11-14'},{'publish_date_kst':'2026-11-16'},{'publish_date_kst':'2026-11-17'}])
  self.assertEqual(days,['2026-11-16','2026-11-17']);self.assertEqual(result['past_or_too_late'],1)
 def test_unknown_receipt_holds_before_any_action(self):
  l={'corgi_delivery':{'mode':m.p.MODE},'corgi_publications':{'a':{'status':'schedule_upload_submitted'}}}
  result,days,recs=self.exercise([{'publish_date_kst':'2026-11-16'}],l)
  self.assertEqual(result['state'],'uncertain_submission_hold');self.assertEqual(days+recs,[])
 def test_reconcile_tick_never_schedules(self):
  l={'corgi_delivery':{'mode':m.p.MODE},'corgi_publications':{'a':{'status':'schedule_verification_pending','video_id':'abcdefghijk'}}}
  _,days,recs=self.exercise([{'publish_date_kst':'2026-11-16'}],l,mode='auto_reconcile')
  self.assertEqual(days,[]);self.assertEqual(recs,['a'])
 def test_scheduled_date_never_resubmitted(self):
  l={'corgi_delivery':{'mode':m.p.MODE},'corgi_publications':{'a':{'status':'scheduled_verified','date_kst':'2026-11-16','publish_at':'2026-11-16T10:00:00Z'}}}
  self.assertEqual(self.exercise([{'publish_date_kst':'2026-11-16'}],l)[1],[])
 def test_uncertain_failure_stops_batch_once(self):self.exercise([{'publish_date_kst':'2026-11-16'},{'publish_date_kst':'2026-11-17'}],failure=True)
 def test_workflow_single_path_and_shared_lock(self):
  text=Path('.github/workflows/corgi.yml').read_text()
  self.assertNotIn("cron: '0 10",text);self.assertNotIn("&& 'publish'",text)
  self.assertIn("cron: '17 * * * *'",text);self.assertIn("cron: '7 10 * * *'",text)
  self.assertIn('group: shorts-production',text);self.assertIn('cancel-in-progress: false',text)

class InvalidReceipt(unittest.TestCase):
 def test_invalid_receipt_id_blocks_before_oauth(self):
  for vid in [None,'','short','abcdefghijk&part=secret']:
   ledger={'corgi_delivery':{'mode':m.p.MODE},'corgi_publications':{'a':{'status':'schedule_verification_pending','video_id':vid}}}
   with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':'auto_reconcile'}),patch.object(m.a.c,'journal_get',return_value=(ledger,'sha')),patch.object(m.a.google,'token') as token:
    with self.assertRaises(ValueError):m.a.reconcile('a')
    token.assert_not_called()
