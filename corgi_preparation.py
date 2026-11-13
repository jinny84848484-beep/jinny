"""Offline preparation and mock adapter only. Live generation is NOT implemented."""
import hashlib
import json
import os
from pathlib import Path
import uuid

API_BASE='https://api.vidogrid.ai/uapi/v1'
LIVE_GENERATION_ENABLED=False

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def plan(prompt):
    if not isinstance(prompt,str) or not prompt.strip():raise ValueError('PROMPT_REQUIRED')
    return {'schema_version':1,'request':{'prompt':prompt,'model':'veo-3.1-fast','resolution':'720p','aspect_ratio':'9:16','duration':8},'budget_credits':0,'cost_approval':None,'status':'draft','content_review':'pending','rights_review':'pending','public_approval':'pending'}

def request_spec(draft):
    body=draft['request']
    if set(body)!={'prompt','model','resolution','aspect_ratio','duration'}:raise ValueError('UNSUPPORTED_FIELDS')
    if not isinstance(body['prompt'],str) or not body['prompt'].strip():raise ValueError('PROMPT_REQUIRED')
    if body['model'] not in ('veo-3.1','veo-3.1-fast') or body['resolution'] not in ('720p','1080p') or body['aspect_ratio'] not in ('9:16','16:9') or type(body['duration']) is not int or body['duration'] not in (4,6,8):raise ValueError('INVALID_VEO_OPTIONS')
    boundary='corgi-offline-'+fingerprint(body)[:24]
    data=b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode() for key,value in body.items())+f'--{boundary}--\r\n'.encode()
    return {'method':'POST','url':API_BASE+'/video-gen/veo','content_type':'multipart/form-data; boundary='+boundary,'body':data,'request_sha256':fingerprint(body)}

def authorize(draft,key):
    spec=request_spec(draft);budget=draft.get('budget_credits',0);approval=draft.get('cost_approval') or {}
    if type(budget) is not int or budget<=0:raise ValueError('ZERO_BUDGET_BLOCKED')
    if approval.get('request_sha256')!=spec['request_sha256'] or approval.get('max_credits')!=budget or not approval.get('owner_reference') or approval.get('decision')!='approve_generation':raise ValueError('EXPLICIT_COST_APPROVAL_REQUIRED')
    if not key:raise ValueError('API_KEY_REQUIRED')
    return spec

def submit_live(draft,key=None):
    authorize(draft,key)
    # No network transport exists here: provider has no verified pre-charge price cap.
    raise RuntimeError('LIVE_ADAPTER_DISABLED_PRICE_CAP_AND_DURABLE_REMOTE_JOURNAL_REQUIRED')

def mock_submission(draft,key,account,receipt_path,transport):
    """Test-only injected fake transport; never call this with a network transport.

    Receipt lives on durable LOCAL storage. Not sufficient for ephemeral CI activation.
    O_EXCL + fsync occurs before accepted POST. Any existing receipt forbids retry.
    """
    spec=authorize(draft,key)
    if account.get('is_active') is not True or account.get('plan_id')!='PP0001':raise ValueError('PREMIUM_ACCOUNT_REQUIRED')
    credits=account.get('user_credit',{}).get('available_credit')
    if type(credits) not in (int,float) or credits<draft['budget_credits']:raise ValueError('INSUFFICIENT_CREDITS')
    path=Path(receipt_path);record={'request_sha256':spec['request_sha256'],'status':'submitted_unknown','mock_only':True}
    with path.open('x') as stream:
        json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
    response=transport(spec) # no automatic retry, even if this raises
    identifier=str(uuid.UUID(response['uuid']))
    record.update(status='accepted_mock',uuid=identifier)
    with path.open('w') as stream:
        json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
    return record

def assess_history(history):
    # Deliberately does not download URLs or promote a provider result into approved queue.
    if history.get('status')!=2:return {'status':'not_ready','public_approval':'pending'}
    files=history.get('generated_video',[])
    return {'status':'awaiting_local_asset_and_review','provider_file_count':len(files),'technical_qa':'pending','content_review':'pending','rights_review':'pending','public_approval':'pending'}

def technical_report(probe):
    videos=[x for x in probe.get('streams',[]) if x.get('codec_type')=='video']
    ok=False
    if len(videos)==1:
        v=videos[0]
        try:ok=v['width']>0 and v['height']>v['width'] and 0<float(probe['format']['duration'])<=180
        except (KeyError,TypeError,ValueError):pass
    return {'technical_probe_pass':ok,'content_review':'pending','rights_review':'pending','public_approval':'pending','publish_ready':False}

if __name__=='__main__':
    # Offline preview only; never interprets environment keys or sends a request.
    print(json.dumps(plan('A gentle original corgi character enjoys a tiny everyday surprise. No distress, no copied character, no third-party footage.'),ensure_ascii=False,indent=2))
