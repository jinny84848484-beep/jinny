"""Approved-artifact only publisher. No generation, payment, or automatic retry."""
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
import cloud_runner as c

ROOT=Path(__file__).resolve().parent
CHANNEL='UCWFQVq3zxSnMi1sa0dUTVMQ'
MAX_BYTES=100*1024*1024
KST=dt.timezone(dt.timedelta(hours=9))

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def local_file(name):
    if not isinstance(name,str):raise ValueError('Asset path missing')
    path=(ROOT/name).resolve()
    if not path.is_relative_to((ROOT/'corgi').resolve()) or not path.is_file():raise ValueError('Only repository corgi assets allowed')
    return path

def approved_item(day):
    queue=json.loads((ROOT/'corgi/queue.json').read_text())
    if queue.get('schema_version')!=1:raise ValueError('Unsupported queue schema')
    items=queue.get('items',[])
    ids=[x.get('id') for x in items]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate queue IDs')
    selected=[x for x in items if x.get('publish_date_kst')==day]
    if len(selected)!=1:raise ValueError('Blocked: exactly one approved artifact required for today')
    item=selected[0]
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',item.get('id','')) or item.get('channel_id')!=CHANNEL:raise ValueError('Invalid item identity')
    path=local_file(item['asset'])
    if path.suffix.lower()!='.mp4' or not 0<path.stat().st_size<=MAX_BYTES:raise ValueError('MP4 must be 1..100 MiB')
    asset_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    if asset_hash!=item.get('sha256'):raise ValueError('Asset hash mismatch')
    meta=item.get('metadata',{})
    if not isinstance(meta.get('title'),str) or not 1<=len(meta['title'])<=100 or not isinstance(meta.get('description'),str) or len(meta['description'])>5000:raise ValueError('Invalid metadata')
    for key in ('made_for_kids','contains_synthetic_media'):
        if type(meta.get(key)) is not bool:raise ValueError('Explicit audience and synthetic disclosure required')
    # Evidence and public approval bind both the exact video and final metadata.
    fingerprint=digest({k:v for k,v in item.items() if k!='approval'})
    approval=item.get('approval',{})
    if approval.get('decision')!='public' or approval.get('manifest_sha256')!=fingerprint or not approval.get('owner_approval_reference'):raise ValueError('Explicit owner public approval missing or stale')
    for kind in ('qa','rights'):
        ref=item.get(kind,{})
        evidence=local_file(ref.get('path'))
        if hashlib.sha256(evidence.read_bytes()).hexdigest()!=ref.get('sha256'):raise ValueError('Evidence hash mismatch')
        data=json.loads(evidence.read_text())
        if data.get('asset_sha256')!=asset_hash or data.get('approved') is not True or not data.get('reviewer') or not data.get('evidence'):raise ValueError('Missing approved QA/rights evidence')
    return item,path,fingerprint

def upload(item,path,token):
    meta=item['metadata'];size=path.stat().st_size
    body={'snippet':{'title':meta['title'],'description':meta['description'],'categoryId':'15'},'status':{'privacyStatus':'public','selfDeclaredMadeForKids':meta['made_for_kids'],'containsSyntheticMedia':meta['contains_synthetic_media']}}
    req=urllib.request.Request('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','X-Upload-Content-Length':str(size),'X-Upload-Content-Type':'video/mp4'},method='POST')
    with urllib.request.urlopen(req,timeout=60) as response:session=response.headers['Location']
    url=urllib.parse.urlparse(session)
    if url.scheme!='https' or url.hostname not in ('www.googleapis.com','youtube.googleapis.com') or url.username or url.password:raise RuntimeError('Unexpected upload endpoint')
    result=c.http(session,path.read_bytes(),{'Authorization':'Bearer '+token,'Content-Type':'video/mp4','Content-Length':str(size)},'PUT')
    vid=result.get('id','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):raise RuntimeError('No valid upload ID; reconcile manually')
    return vid

def run():
    if os.environ.get('GITHUB_REF')!='refs/heads/main':raise RuntimeError('Publisher requires main')
    now=dt.datetime.now(KST);day=now.date().isoformat()
    item,path,fingerprint=approved_item(day) # fail closed before even OAuth
    if os.environ.get('CORGI_MODE','validate')!='publish':
        print('Approved corgi artifact validated; no upload requested');return
    if now.hour!=19:raise RuntimeError('OUTSIDE_WINDOW')
    ledger,sha=c.journal_get();records=ledger.setdefault('corgi_publications',{})
    if any(r.get('status')!='complete' for r in records.values()):raise RuntimeError('Uncertain prior submission: reconcile manually, no retry')
    if item['id'] in records or any(r.get('date_kst')==day or r.get('asset_sha256')==item['sha256'] for r in records.values()):raise RuntimeError('Duplicate item/date/asset blocked')
    token=c.youtube_token('korean')
    if c.verify_channel('korean',token)!=CHANNEL:raise RuntimeError('Wrong corgi channel')
    record={'status':'upload_submitted','date_kst':day,'asset_sha256':item['sha256'],'manifest_sha256':fingerprint,'channel_id':CHANNEL,'submitted_at':now.isoformat()}
    records[item['id']]=record
    sha=c.journal_put(ledger,sha) # durable write MUST succeed before upload
    vid=upload(item,path,token)
    record.update(status='verification_pending',video_id=vid)
    sha=c.journal_put(ledger,sha) # preserve ID even if visibility verification fails
    result=c.http('https://www.googleapis.com/youtube/v3/videos?part=snippet,status&id='+vid,headers={'Authorization':'Bearer '+token})
    videos=result.get('items',[])
    if len(videos)!=1 or videos[0].get('id')!=vid or videos[0].get('snippet',{}).get('channelId')!=CHANNEL or videos[0].get('status',{}).get('privacyStatus')!='public':raise RuntimeError('Public video verification incomplete; inspect Studio')
    record.update(status='complete',url='https://www.youtube.com/shorts/'+vid,verified_at=dt.datetime.now(KST).isoformat())
    c.journal_put(ledger,sha)
    print('Verified corgi publication: '+record['url'])

if __name__=='__main__':
    try:run()
    except Exception as exc:
        # Classify only known local errors; never echo provider text/session credentials.
        code='AUTH_OR_UPLOAD_UNCERTAIN'
        if isinstance(exc,ValueError):code='EMPTY_QUEUE' if 'exactly one' in str(exc) else 'INVALID_APPROVAL_OR_EVIDENCE'
        elif isinstance(exc,FileNotFoundError):code='MISSING_ASSET_OR_EVIDENCE'
        elif str(exc)=='OUTSIDE_WINDOW':code='OUTSIDE_WINDOW'
        raise SystemExit('Corgi blocked: '+code+'. Inspect approved queue and durable ledger; never retry uncertain submissions.')
