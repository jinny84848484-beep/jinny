import copy,datetime as dt,os,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
import corgi_schedule_adapter as a
import corgi_schedule_plan as p
class Adapter(unittest.TestCase):
 def fixture(self):
  item={'id':'a','sha256':'hash','metadata':{'title':'T','description':'D','made_for_kids':False,'contains_synthetic_media':True}};l={'corgi_delivery':{'mode':p.MODE},'corgi_publications':{}}
  return item,l
 def response(self):return {'items':[{'id':'abcdefghijk','snippet':{'channelId':p.legacy.CHANNEL,'title':'T','description':'D'},'status':{'selfDeclaredMadeForKids':False,'containsSyntheticMedia':True,'privacyStatus':'private','publishAt':'2026-11-16T10:00:00Z','uploadStatus':'processed'},'processingDetails':{'processingStatus':'succeeded'}}]}
 def exercise(self,cas_fail=False,upload_fail=False,verify_fail=False,late=False,receipt_cas_fail=False):
  item,l=self.fixture();saved=[];calls=[];clock_values=[dt.datetime(2026,11,15,23,tzinfo=p.KST)]*2+[dt.datetime(2026,11,16,18,tzinfo=p.KST) if late else dt.datetime(2026,11,16,0,tzinfo=p.KST)]*3
  def clock():return clock_values.pop(0)
  def put(ledger,sha):
   calls.append('CAS')
   if cas_fail or (receipt_cas_fail and calls.count('CAS')==2):raise RuntimeError('CAS conflict')
   saved.append(copy.deepcopy(ledger));return str(len(saved))
  def upload(*args,**kw):
   calls.append('UPLOAD');self.assertEqual(kw,{'publish_at':'2026-11-16T10:00:00Z'})
   if upload_fail:raise TimeoutError('response lost')
   return 'abcdefghijk'
  with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'CORGI_RECEIPT_DIR':td}),patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':'schedule'}),patch.object(a.c,'journal_get',return_value=(l,'old')),patch.object(a.c,'journal_put',side_effect=put),patch.object(a.google,'token',return_value='neverlogged'),patch.object(a.google,'verify_channel',return_value=p.legacy.CHANNEL),patch.object(a,'channel_inventory',return_value=[]),patch.object(p.legacy,'approved_item',return_value=(item,Path('/fixture.mp4'),'manifest')),patch.object(p,'scheduling_quality'),patch.object(p.legacy,'upload',side_effect=upload),patch.object(a,'videos',side_effect=RuntimeError('readback failure') if verify_fail else None,return_value=self.response()):
   if cas_fail or upload_fail or verify_fail or late or receipt_cas_fail:
    with self.assertRaises((ValueError,RuntimeError,TimeoutError)):a.schedule('2026-11-16',clock)
   else:
    rec=a.schedule('2026-11-16',clock);self.assertEqual(rec['status'],'scheduled_verified')
   if receipt_cas_fail:
    files=list(Path(td).glob('*.json'));self.assertEqual(len(files),1)
    receipt=json.loads(files[0].read_text());self.assertEqual(receipt['video_id'],'abcdefghijk');self.assertNotIn('neverlogged',files[0].read_text())
    self.assertEqual(calls.count('UPLOAD'),1);self.assertEqual(saved[-1]['corgi_publications']['a']['status'],'schedule_upload_submitted')
  if cas_fail or late:self.assertNotIn('UPLOAD',calls)
  if upload_fail:self.assertEqual(calls.count('UPLOAD'),1);self.assertEqual(saved[-1]['corgi_publications']['a']['status'],'schedule_upload_submitted')
  if verify_fail:self.assertEqual(saved[-1]['corgi_publications']['a']['video_id'],'abcdefghijk');self.assertEqual(saved[-1]['corgi_publications']['a']['status'],'schedule_verification_pending')
  if not (cas_fail or upload_fail or verify_fail or late or receipt_cas_fail):self.assertEqual(calls,['CAS','UPLOAD','CAS','CAS'])
 def test_receipt_cas_failure_preserves_artifact_no_retry(self):self.exercise(receipt_cas_fail=True)
 def test_cas_upload_receipt_verify_order_midnight(self):self.exercise()
 def test_cas_conflict_no_upload(self):self.exercise(cas_fail=True)
 def test_late_target_no_upload(self):self.exercise(late=True)
 def test_response_loss_no_retry(self):self.exercise(upload_fail=True)
 def test_receipt_survives_verification_failure(self):self.exercise(verify_fail=True)
 def test_inventory_manual_scheduled_and_unknown_private_rejected(self):
  for st in [{'privacyStatus':'private'},{'privacyStatus':'private','publishAt':'2026-11-16T10:00:00Z'},{'privacyStatus':'unlisted'}]:
   with self.assertRaises(ValueError):a.check_inventory([{'id':'abcdefghijk','snippet':{'channelId':p.legacy.CHANNEL},'status':st}],'2026-11-16',{})
 def test_reconcile_unknown_id_never_calls_upload_or_oauth(self):
  with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':'reconcile'}),patch.object(a.c,'journal_get',return_value=(self.fixture()[1],'sha')),patch.object(a.google,'token') as token,patch.object(p.legacy,'upload') as upload:
   with self.assertRaises(ValueError):a.reconcile('missing')
   token.assert_not_called();upload.assert_not_called()

class LegacyExclusion(unittest.TestCase):
 def test_scheduled_ledger_blocks_legacy_before_oauth(self):
  import corgi_runner as r
  with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':'publish'}),patch.object(r.dt,'datetime') as clock,patch.object(r,'approved_item',return_value=({},Path('/fixture'),'fp')),patch.object(r.c,'journal_get',return_value=({'corgi_delivery':{'mode':p.MODE}},'sha')),patch.object(r.c,'youtube_token') as token,patch.object(r,'upload') as upload:
   clock.now.return_value.hour=19;clock.now.return_value.date.return_value.isoformat.return_value='2026-11-16'
   with self.assertRaises(ValueError):r.run()
   token.assert_not_called();upload.assert_not_called()
class UploadTransport(unittest.TestCase):
 def test_private_schedule_body_and_approved_bytes_only(self):
  import tempfile,hashlib,json,contextlib,corgi_runner as r
  import corgi_google_transport as google

  from unittest.mock import MagicMock
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'v.mp4';path.write_bytes(b'mp4')
   item={'sha256':hashlib.sha256(b'mp4').hexdigest(),'metadata':{'title':'T','description':'D','made_for_kids':False,'contains_synthetic_media':True}}
   response=MagicMock();response.__enter__.return_value.headers={'Location':'https://www.googleapis.com/upload/session'}
   future=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
   with patch.object(google,'open_request',return_value=response) as start,patch.object(google,'http',return_value={'id':'abcdefghijk'}) as put:
    self.assertEqual(r.upload(item,path,'secret',publish_at=future),'abcdefghijk')
    body=json.loads(start.call_args.args[0].data);self.assertEqual(body['status']['privacyStatus'],'private');self.assertEqual(body['status']['publishAt'],future);self.assertEqual(put.call_args.args[1],b'mp4')
   path.write_bytes(b'tampered')
   with patch.object(google,'open_request') as start:
    with self.assertRaises(ValueError):r.upload(item,path,'secret',publish_at=future)
    start.assert_not_called()

class CredentialBoundary(unittest.TestCase):
 def test_all_untrusted_google_endpoints_rejected_before_network(self):
  import corgi_google_transport as g
  for url in ['http://www.googleapis.com/x','https://evil.test/x','https://www.googleapis.com:444/x','https://user@www.googleapis.com/x','https://www.googleapis.com/x#secret']:
   with patch.object(g.urllib.request,'build_opener') as build:
    with self.assertRaises(ValueError):g.http(url,headers={'Authorization':'secret'})
    build.assert_not_called()
 def test_redirect_never_forwards_credentials(self):
  import corgi_google_transport as g
  for host in g.HOSTS:
   request=g.urllib.request.Request('https://'+host+'/x',headers={'Authorization':'secret'},data=b'refresh_secret')
   with self.assertRaises(RuntimeError):g.NoRedirect().redirect_request(request,None,302,'redirect',{},'https://evil.test/')
 def test_transport_installs_redirect_rejection_for_oauth_and_reads(self):
  import corgi_google_transport as g
  from unittest.mock import MagicMock
  response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
  with patch.object(g.urllib.request,'build_opener') as build:
   build.return_value.open.return_value=response
   for host in g.HOSTS:g.http('https://'+host+':443/x',headers={'Authorization':'secret'})
   self.assertEqual(build.call_count,3)
   for call in build.call_args_list:self.assertIsInstance(call.args[0],g.NoRedirect)
 def test_workflow_receipts_always_preserved(self):
  text=Path('.github/workflows/corgi.yml').read_text()
  self.assertIn('if: always()',text);self.assertIn('corgi-receipts/*.json',text);self.assertIn('retention-days: 90',text)
