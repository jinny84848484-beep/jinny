import copy,datetime,json,tempfile,unittest,hashlib,io,zipfile,os
from pathlib import Path
from unittest.mock import patch
import cloud_runner as c
import research_manifest_recovery_20261115 as r
import research_recovery_registry as registry
class TodayRecovery(unittest.TestCase):
 def setUp(self):
  self.ledger={'days':{r.DAY:{'status':'research_validation_failed','retry_hold':True,'checkpoints':{},'research_failures':[{'reason':'fixture_only','repair_attempted':True}]}},'publications':[]}
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:
   for stage in ('research','research_repair'):
    request_hash=hashlib.sha256(stage.encode()).hexdigest()
    raw=json.dumps({'day':r.DAY,'stage':stage,'request_hash':request_hash,'result':{'text':'synthetic offline fixture; not editorial evidence','sources':[]}}).encode()
    z.writestr(r.DAY+'/checkpoints/'+stage+'.json',raw)
    self.ledger['days'][r.DAY]['checkpoints'][stage]={'status':'completed','request_hash':request_hash,'sha256':hashlib.sha256(raw).hexdigest()}
  self.raw=stream.getvalue()
  for name,value in [('ARCHIVE_SIZE',len(self.raw)),('ARCHIVE_SHA256',hashlib.sha256(self.raw).hexdigest()),('RECORD_HASH',c.fingerprint(self.ledger['days'][r.DAY]))]:
   patcher=patch.object(r,name,value);patcher.start();self.addCleanup(patcher.stop)
 def exercise(self,mutate=None,cas_fail=False,dirty=False,graphics=True):
  ledger=copy.deepcopy(self.ledger);before=copy.deepcopy(ledger);events=[]
  if mutate:mutate(ledger)
  def save(*args):
   events.append('cas')
   if cas_fail:raise RuntimeError('CAS conflict')
  def produce(**kw):
   self.assertEqual(kw,{'expected_day':r.DAY});events.append('produce')
  with tempfile.TemporaryDirectory() as d,patch.object(c.p,'ROOT',Path(d)),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,12,tzinfo=c.p.KST)),patch.object(c,'journal_get',return_value=(ledger,'expected-sha')),patch.object(c,'journal_put',side_effect=save) as put,patch.object(c,'preflight') as pre,patch.object(c,'produce',side_effect=produce) as run,patch.object(c.daily_graphics,'enabled',return_value=graphics):
   if dirty:(Path(d)/'runs'/r.DAY/'checkpoints').mkdir(parents=True)
   if mutate or cas_fail or dirty or not graphics:
    with self.assertRaises((ValueError,RuntimeError)):r.recover_from_archive(self.raw)
    run.assert_not_called()
    if not cas_fail:put.assert_not_called()
    return
   r.recover_from_archive(self.raw)
   self.assertEqual(events,['cas','produce']);put.assert_called_once_with(ledger,'expected-sha')
   rec=ledger['days'][r.DAY]
   self.assertEqual(rec['checkpoints'],before['days'][r.DAY]['checkpoints'])
   self.assertEqual(rec['research_failures'],before['days'][r.DAY]['research_failures'])
   self.assertEqual(rec['research_recovery']['previous_record'],before['days'][r.DAY])
   self.assertFalse(rec['retry_hold']);self.assertEqual(rec['status'],'research_recovered')
   self.assertEqual(len(registry.recovered_manifest(rec,set())['stories']),2)
   jobs=[{'id':r.DAY+'-ojirap-'+str(i),'channel':'ojirap','sources':x['sources']} for i,x in enumerate(r.load_bundle()['manifest']['stories'])]
   registry.write_source_packets(rec,jobs)
   self.assertTrue((Path(d)/'runs'/r.DAY/'sources'/(jobs[0]['id']+'.json')).is_file())
   self.assertEqual(set(x.name for x in (Path(d)/'runs'/r.DAY/'recovery'/'originals').iterdir()),{'research.json','research_repair.json'})
   with self.assertRaisesRegex(ValueError,'exact'):r.recover_from_archive(self.raw)
   self.assertEqual(events,['cas','produce'])
 def test_midnight_before_cas_no_write_or_provider(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c.p,'ROOT',Path(d)),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,23,59,tzinfo=c.p.KST)) as clock,patch.object(c,'journal_get',return_value=(copy.deepcopy(self.ledger),'sha')),patch.object(c,'journal_put') as put,patch.object(c,'produce') as run,patch.object(c.daily_graphics,'enabled',return_value=True):
   def advance():clock.return_value=datetime.datetime(2026,11,16,tzinfo=c.p.KST)
   with patch.object(c,'preflight',side_effect=advance):
    with self.assertRaisesRegex(ValueError,'before CAS'):r.recover_from_archive(self.raw)
   put.assert_not_called();run.assert_not_called()
 def test_full_restore_registry_and_single_cas_before_providers(self):self.exercise()
 def test_cas_conflict_no_provider(self):self.exercise(cas_fail=True)
 def test_disabled_policy_no_write(self):self.exercise(graphics=False)
 def test_dirty_directory_no_write(self):self.exercise(dirty=True)
 def test_changed_hold_no_write(self):self.exercise(mutate=lambda l:l['days'][r.DAY].update(retry_hold=False))
 def test_existing_publication_no_write(self):self.exercise(mutate=lambda l:l['publications'].append({'job_id':r.DAY+'-ojirap'}))
 def test_registry_rejects_foreign_and_wrong_hash(self):
  for rec in [{},{'research_recovery':{'source_run':r.SOURCE_RUN,'bundle_sha256':'wrong'}}]:
   with self.assertRaises(ValueError):registry.resolve(rec)
 def test_old_recovery_remains_resolvable(self):
  import research_manifest_recovery as old
  self.assertIs(registry.resolve({'research_recovery':{'source_run':old.SOURCE_RUN,'bundle_sha256':old.BUNDLE_SHA256}}),old)
 def test_foreign_channel_packet_rejected(self):
  with patch.object(c.daily_graphics,'enabled',return_value=True):
   with self.assertRaisesRegex(ValueError,'scope'):r.write_source_packets([{'id':r.DAY+'-corgi','channel':'corgi','sources':[]}])
 def test_invalid_archive_rejected_before_io(self):
  with patch.object(c,'journal_get') as get,patch.object(c.p,'now',return_value=datetime.datetime(2026,11,15,tzinfo=c.p.KST)):
   with self.assertRaisesRegex(ValueError,'hash'):r.recover_from_archive(b'wrong')
   get.assert_not_called()

class RecoveryPolicy(unittest.TestCase):
 def test_policy_only_explicit_today_mode(self):
  import daily_graphics as g
  for mode,day,expected in [('produce',15,False),('recover_research_manifest',15,False),('recover_research_manifest_20261115',15,True),('recover_research_manifest_20261115',16,False)]:
   with patch.dict(os.environ,{'RUN_MODE':mode}),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,day,tzinfo=c.p.KST)):
    self.assertEqual(g.enabled(),expected)
 def test_workflow_preserves_old_and_adds_new_entry(self):
  text=(c.p.ROOT/'.github/workflows/shorts.yml').read_text()
  self.assertIn('python research_manifest_recovery.py',text)
  self.assertIn('python research_manifest_recovery_20261115.py',text)
  self.assertIn('group: shorts-production',text)

class RecoveryDateBoundary(unittest.TestCase):
 def test_after_cas_midnight_fails_before_journal_or_provider(self):
  with patch.dict(os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,16,tzinfo=c.p.KST)),patch.object(c,'journal_get') as get,patch.object(c,'preflight') as pre:
   with self.assertRaisesRegex(RuntimeError,'date changed'):c.produce(expected_day='2026-11-15')
   get.assert_not_called();pre.assert_not_called()
