import copy,datetime as dt,unittest
from unittest.mock import patch
from pathlib import Path
import corgi_schedule_plan as p
class Scheduled(unittest.TestCase):
 def setUp(self):
  self.now=dt.datetime(2026,11,15,12,tzinfo=p.KST);self.l={'corgi_delivery':{'mode':p.MODE},'corgi_publications':{}}
  self.item={'id':'corgi-a','sha256':'hash','metadata':{'title':'corgi','description':'approved','made_for_kids':False,'contains_synthetic_media':True}}
 def plan(self):
  with patch.object(p.legacy,'approved_item',return_value=(self.item,Path('/fixture.mp4'),'approved-manifest')),patch.object(p,'scheduling_quality'):return p.plan('2026-11-15',self.l,self.now)
 def record(self):return p.receipt(self.plan()['reserved_ledger']['corgi_publications']['corgi-a'],'abcdefghijk')
 def response(self):return {'items':[{'id':'abcdefghijk','snippet':{'channelId':p.legacy.CHANNEL,'title':'corgi','description':'approved'},'status':{'selfDeclaredMadeForKids':False,'containsSyntheticMedia':True,'privacyStatus':'private','publishAt':'2026-11-15T10:00:00Z','uploadStatus':'processed'},'processingDetails':{'processingStatus':'succeeded'}}]}
 def test_19kst_equals_10utc_private_only_no_state_mutation(self):
  before=copy.deepcopy(self.l);x=self.plan();self.assertEqual(self.l,before);self.assertEqual(x['upload_body']['status']['publishAt'],'2026-11-15T10:00:00Z');self.assertEqual(x['upload_body']['status']['privacyStatus'],'private');self.assertFalse(x['may_execute'])
 def test_empty_queue_and_rights_failures_propagate(self):
  with patch.object(p.legacy,'approved_item',side_effect=ValueError('no approved asset')):
   with self.assertRaises(ValueError):p.plan('2026-11-15',self.l,self.now)
 def test_mode_exclusive(self):
  with self.assertRaises(ValueError):p.require_legacy(self.l)
  p.require_legacy({});self.l={}
  with self.assertRaises(ValueError):self.plan()
 def test_late_schedule_never_becomes_immediate_upload(self):
  for hour in (18,19,22):
   self.now=self.now.replace(hour=hour)
   with self.assertRaises(ValueError):self.plan()
 def test_duplicates_and_uncertain_states_block(self):
  for r in [{'status':'scheduled_verified','date_kst':'2026-11-15'},{'status':'complete','asset_sha256':'hash'},{'status':'schedule_upload_submitted'}]:
   self.l['corgi_publications']={'other':r}
   with self.assertRaises(ValueError):self.plan()
 def test_future_scheduled_is_not_public_complete(self):self.assertEqual(p.verify(self.record(),self.response(),self.now)['status'],'scheduled_verified')
 def test_processing_wrong_channel_wrong_time_rejected(self):
  for change in [lambda v:v['snippet'].update(channelId='wrong'),lambda v:v['status'].update(publishAt='2026-11-15T11:00:00Z'),lambda v:v['processingDetails'].update(processingStatus='processing'),lambda v:v['status'].update(privacyStatus='public')]:
   r=self.response();change(r['items'][0])
   with self.assertRaises(ValueError):p.verify(self.record(),r,self.now)
 def test_public_completion_only_after_target(self):
  r=self.response();r['items'][0]['status']['privacyStatus']='public';self.assertEqual(p.verify(self.record(),r,self.now.replace(hour=19))['status'],'complete')
 def test_no_naive_times(self):
  with self.assertRaises(ValueError):p.plan('2026-11-15',self.l,dt.datetime(2026,11,15))
class Quality(unittest.TestCase):
 def test_all_motion_identity_and_music_fields_required(self):
  import json,tempfile
  with tempfile.TemporaryDirectory() as d:
   q=Path(d)/'qa.json';r=Path(d)/'rights.json';qa={k:True for k in ('actual_motion','original_corgi_identity','short_tail','not_slideshow','resolution_1080p')};q.write_text(json.dumps(qa));r.write_text(json.dumps({'music_license_verified':True}));item={'qa':{'path':'qa'},'rights':{'path':'rights'}}
   with patch.object(p.legacy,'local_file',side_effect=lambda s:q if s=='qa' else r):
    p.scheduling_quality(item)
    for key in qa:
     bad=dict(qa);bad[key]=False;q.write_text(json.dumps(bad))
     with self.assertRaises(ValueError):p.scheduling_quality(item)
    q.write_text(json.dumps(qa));r.write_text('{}')
    with self.assertRaises(ValueError):p.scheduling_quality(item)
