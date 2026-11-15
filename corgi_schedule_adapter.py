"""Explicit single-authority YouTube scheduling adapter; no generation or retries."""
import datetime as dt,hashlib,os,urllib.parse
import cloud_runner as c
import corgi_google_transport as google
import json, pathlib, re
import corgi_schedule_plan as p
import corgi_runner as legacy

def now():return dt.datetime.now(p.KST)
def assert_context():
 if os.environ.get('GITHUB_REF')!='refs/heads/main':raise ValueError('main required')
 if os.environ.get('CORGI_MODE') not in {'schedule','reconcile','auto_schedule','auto_reconcile'}:raise ValueError('Explicit scheduling mode required')
def videos(ids,token):
 return google.http('https://www.googleapis.com/youtube/v3/videos?part=snippet,status,processingDetails&id='+','.join(ids),headers={'Authorization':'Bearer '+token})
def channel_inventory(token):
 """Owner-authorized inventory, including private videos; bound pagination fails closed."""
 headers={'Authorization':'Bearer '+token}
 channels=google.http('https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id='+legacy.CHANNEL,headers=headers).get('items',[])
 if len(channels)!=1 or channels[0].get('id')!=legacy.CHANNEL:raise ValueError('Inventory channel mismatch')
 playlist=channels[0].get('contentDetails',{}).get('relatedPlaylists',{}).get('uploads')
 if not playlist:raise ValueError('Uploads playlist missing')
 ids=[];page=''
 for _ in range(5):
  url='https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&maxResults=50&playlistId='+urllib.parse.quote(playlist,safe='')
  if page:url+='&pageToken='+urllib.parse.quote(page,safe='')
  result=google.http(url,headers=headers)
  for entry in result.get('items',[]):
   vid=entry.get('contentDetails',{}).get('videoId')
   if not vid:raise ValueError('Incomplete inventory item')
   ids.append(vid)
  page=result.get('nextPageToken')
  if not page:break
 if page:raise ValueError('Inventory exceeds bounded complete scan')
 out=[]
 for i in range(0,len(ids),50):out+=videos(ids[i:i+50],token).get('items',[])
 if len(out)!=len(ids) or {x.get('id') for x in out}!=set(ids):raise ValueError('Incomplete video inventory')
 return out

def check_inventory(items,day,ledger):
 known={x.get('video_id'):x for x in ledger.get('corgi_publications',{}).values() if x.get('video_id')}
 for v in items:
  if v.get('snippet',{}).get('channelId')!=legacy.CHANNEL:raise ValueError('Foreign channel in inventory')
  status=v.get('status',{});privacy=status.get('privacyStatus')
  if privacy=='public':
   published=v.get('snippet',{}).get('publishedAt')
   if not published:raise ValueError('Public date unknown')
   if p.instant(published).astimezone(p.KST).date().isoformat()==day:raise ValueError('Already public video on target day')
  elif privacy=='private' and status.get('publishAt'):
   if p.instant(status['publishAt']).astimezone(p.KST).date().isoformat()==day:raise ValueError('Existing scheduled video on target day')
   if v['id'] not in known:raise ValueError('Unreconciled external scheduled video')
  else:raise ValueError('Unreconciled private/unlisted video')

def save_receipt(item_id,record):
 # Only allowlisted nonsecret fields. Persist before any remote receipt CAS.
 root=pathlib.Path(os.environ.get('CORGI_RECEIPT_DIR','corgi-receipts'));root.mkdir(parents=True,exist_ok=True)
 data={k:record[k] for k in ('video_id','channel_id','asset_sha256','manifest_sha256','date_kst','publish_at','status')}
 data['item_id']=item_id
 name=hashlib.sha256((item_id+record['video_id']).encode()).hexdigest()+'.json'
 with (root/name).open('x') as f:
  json.dump(data,f,sort_keys=True);f.flush();os.fsync(f.fileno())
 fd=os.open(root,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def schedule(day,clock=now):
 assert_context()
 if os.environ.get('CORGI_MODE') not in {'schedule','auto_schedule'}:raise ValueError('Schedule mode required')
 ledger,sha=c.journal_get();plan=p.plan(day,ledger,clock())
 token=google.token('korean')
 if google.verify_channel('korean',token)!=legacy.CHANNEL:raise ValueError('Wrong corgi channel')
 check_inventory(channel_inventory(token),day,ledger)
 # Confirm exact approved bytes again after slow read checks, before durable reservation.
 replan=p.plan(day,ledger,clock())
 for key in ('expected_ledger_hash','item_id','asset_path','upload_body'):
  if replan[key]!=plan[key]:raise ValueError('Approval/time state changed during reconciliation')
 a=replan['reserved_ledger']['corgi_publications'][plan['item_id']];b=plan['reserved_ledger']['corgi_publications'][plan['item_id']]
 if any(a[k]!=b[k] for k in ('asset_sha256','manifest_sha256','publish_at','channel_id')):raise ValueError('Approved reservation changed')
 # submitted_at can differ between clock calls: only compare immutable reservation identity.
 sha=c.journal_put(plan['reserved_ledger'],sha)
 ledger=plan['reserved_ledger'];record=ledger['corgi_publications'][plan['item_id']]
 when=p.instant(record['publish_at'])
 if when-clock().astimezone(dt.timezone.utc)<p.LEAD:raise ValueError('Scheduling lead elapsed; reservation held, no API insert')
 item,path,fp=legacy.approved_item(day);p.scheduling_quality(item)
 if item['sha256']!=record['asset_sha256'] or fp!=record['manifest_sha256']:raise ValueError('Approved artifact changed after CAS')
 vid=legacy.upload(item,path,token,publish_at=record['publish_at'])
 record=p.receipt(record,vid);save_receipt(plan['item_id'],record);ledger['corgi_publications'][plan['item_id']]=record
 sha=c.journal_put(ledger,sha) # NEVER gate successful receipt on deadline
 result=videos([vid],token)
 record=p.verify(record,result,clock());ledger['corgi_publications'][plan['item_id']]=record
 c.journal_put(ledger,sha)
 return record

def reconcile(item_id,clock=now):
 assert_context()
 if os.environ.get('CORGI_MODE') not in {'reconcile','auto_schedule','auto_reconcile'}:raise ValueError('Reconcile mode required')
 ledger,sha=c.journal_get()
 if p.mode(ledger)!=p.MODE:raise ValueError('Scheduled delivery mode required')
 rec=ledger.get('corgi_publications',{}).get(item_id,{})
 if rec.get('status') not in {'schedule_verification_pending','scheduled_verified'}:raise ValueError('No known receipt; manual reconcile required, never reupload')
 if not isinstance(rec.get('video_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',rec['video_id']):raise ValueError('Invalid receipt ID; manual reconciliation required')
 token=google.token('korean')
 if google.verify_channel('korean',token)!=legacy.CHANNEL:raise ValueError('Wrong corgi channel')
 result=p.verify(rec,videos([rec['video_id']],token),clock())
 ledger['corgi_publications'][item_id]=result;c.journal_put(ledger,sha)
 return result
