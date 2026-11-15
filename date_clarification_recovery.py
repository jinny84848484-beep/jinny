"""Pinned opt-in date correction. Preserve prior FAIL; obtain two NEW reviews."""
import copy,hashlib,io,json,os,zipfile
import cloud_runner as c
import date_narration_fix as fix
import research_manifest_recovery_20261115 as original
DAY='2026-11-15'
SOURCE_RUN='43684366776'
HEAD='c49662ff5e17f30fe1ad844d8c4bdfffa6df1e3b'
ARTIFACT=14149911491
SIZE=42924
SHA='e7d3bdc998638210b64d9380119eef1a92662382c5ca13c3d4b102ec3b05f87b'
RECORD='53a3ac8e59c1450a5a000e07e88c6fe2ab579d379c4f1dc2c2c5482b2dd6ef33'
CORRECTED='1a2036abdb7e18115c624c29e3b43ab21f748627b0fbdc73ea6999c4244ea468'
def scope(*_):
 if os.environ.get('RUN_MODE')!='clarify_date_20261115' or os.environ.get('PUBLIC_UPLOADS_ENABLED')!='true':raise ValueError('Explicit correction mode and publication authorization required')
 if c.p.now().date().isoformat()!=DAY:raise ValueError('Correction date changed')
 # Same-day food-event wording must be separately revised after closing time.
 if c.p.now().hour>=17:raise ValueError('ASEAN event closing time reached; new wording review required')
def publication_scope(stage):
 # Once upload returned a video ID, persist its receipt even across cutoff.
 # Every subsequent new job/upload still calls scope and remains blocked.
 if stage in {'publication_marker','completion_marker'}:
  return
 scope()
def recover(raw):
 scope()
 if len(raw)!=SIZE or hashlib.sha256(raw).hexdigest()!=SHA:raise ValueError('Correction archive mismatch')
 ledger,sha=c.journal_get();record=ledger.get('days',{}).get(DAY,{})
 if c.fingerprint(record)!=RECORD:raise ValueError('Exact failure record required')
 if record.get('status')!='editorial_review_failed' or record.get('retry_hold') is not True or record.get('jobs'):raise ValueError('Failure scope mismatch')
 if any(str(x.get('job_id','')).startswith(DAY) or str(x.get('at','')).startswith(DAY) for x in ledger.get('publications',[])):raise ValueError('Publication conflict')
 c.assert_no_unresolved_additional(ledger)
 files={}
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  if len(z.namelist())!=len(set(z.namelist())):raise ValueError('Duplicate archive entries')
  for x in z.infolist():
   if x.is_dir() or not x.filename.startswith(DAY+'/') or '..' in x.filename.split('/') or x.file_size>1000000:raise ValueError('Unsafe archive member')
   files[x.filename]=z.read(x)
 for stage,entry in record['checkpoints'].items():
  folder='recovery/originals/' if stage in ('research','research_repair') else 'checkpoints/'
  raw_cp=files[DAY+'/'+folder+stage+'.json'];cp=json.loads(raw_cp)
  if hashlib.sha256(raw_cp).hexdigest()!=entry['sha256'] or cp.get('day')!=DAY or cp.get('stage')!=stage or cp.get('request_hash')!=entry['request_hash']:raise ValueError('Checkpoint mismatch')
 saved=json.loads(files[DAY+'/checkpoints/editorial-review-input.json'])
 if c.fingerprint(saved)!=record['review_input_hash']:raise ValueError('Review input mismatch')
 corrected=fix.corrected_request(saved)
 if c.fingerprint(corrected)!=CORRECTED:raise ValueError('Correction diff mismatch')
 jobs=corrected['jobs'];c.p.assert_active_news_jobs(jobs);c.validate_batch(copy.deepcopy(jobs),DAY,c.recent_topics(ledger))
 original.recovered_manifest(record,c.recent_topics(ledger))
 run_dir=c.p.ROOT/'runs'/DAY
 if run_dir.exists():raise ValueError('Clean directory required')
 c.preflight();scope()
 for name,payload in files.items():
  p=c.p.ROOT/'runs'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
 c.p.write(run_dir/'recovery/date-clarification-original-record.json',record)
 record['date_clarification']={'source_run':SOURCE_RUN,'artifact_id':ARTIFACT,'archive_sha256':SHA,'previous_record':copy.deepcopy(record),'previous_record_hash':RECORD,'original_input_hash':fix.INPUT_HASH,'corrected_input_hash':CORRECTED,'maximum_new_review_requests':2}
 scope();sha=c.journal_put(ledger,sha)
 # Hold stays set until normal review_jobs yields two real PASS decisions.
 sha=c.review_jobs(ledger,sha,DAY,jobs,correction_namespace='date_clarification_v1')
 scope();c.publish_reviewed(ledger,sha,DAY,stage_callback=publication_scope)
if __name__=='__main__':
 scope()
 recover(original.download_archive(source_run=SOURCE_RUN,source_head=HEAD,artifact_id=ARTIFACT,archive_sha256=SHA,archive_size=SIZE))
