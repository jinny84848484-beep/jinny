"""One owner-authorized draft retry after observed HTTP 429; never automatic retry.
Original outcome/billing remains unknown. No new research, no payment changes.
"""
import copy,hashlib,io,json,zipfile
import cloud_runner as c
from research_manifest_recovery_20261116 import download_archive
DAY='2026-11-17'
RUN='43976150754'
HEAD='73cfd01a68dadf8e664dc9720961028dff2a0b77'
ARTIFACT=14279194944
ARCHIVE_HASH='9fd5fa5152c3a72a1889afe776827336168cafeafd9f457882da9c7e0db78671'
RECORD_HASH='1969066d97bbaa66f86c3070fed01defbc1a0110aa42cdd5369d38c664802182'

def verify_inputs(raw,ledger):
    if len(raw)!=43941 or hashlib.sha256(raw).hexdigest()!=ARCHIVE_HASH:raise ValueError('Recovery archive mismatch')
    rec=ledger.get('days',{}).get(DAY,{})
    if c.fingerprint(rec)!=RECORD_HASH:raise ValueError('Exact unrecovered record required')
    if rec.get('status')!='draft_submitted' or rec.get('jobs') or rec.get('draft_429_recovery'):raise ValueError('Recovery state rejected')
    if any(str(r.get('job_id','')).startswith(DAY) or str(r.get('at','')).startswith(DAY) for r in ledger.get('publications',[])):raise ValueError('Existing publication conflicts')
    c.assert_no_unresolved_additional(ledger)
    restored={}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=len(set(z.namelist())):raise ValueError('Duplicate archive paths')
        if DAY+'/checkpoints/draft.json' in z.namelist():raise ValueError('Completed draft exists; do not resubmit')
        for stage in ('research','research_repair'):
            entry=rec['checkpoints'][stage];saved=z.read(DAY+'/checkpoints/'+stage+'.json');v=json.loads(saved)
            if entry.get('status')!='completed' or hashlib.sha256(saved).hexdigest()!=entry['sha256'] or v.get('day')!=DAY or v.get('stage')!=stage or v.get('request_hash')!=entry['request_hash']:raise ValueError('Completed checkpoint mismatch')
            restored[stage]=saved
        request=json.loads(z.read(DAY+'/checkpoints/draft-input.json'))
    if c.fingerprint(request)!=rec['checkpoints']['draft']['request_hash'] or request.get('instructions')!=c.PROMPT:raise ValueError('Original draft request changed')
    manifest=json.loads(request['prompt'].split('\nRESEARCH MANIFEST:\n',1)[1])
    evidence=[e for v in restored.values() for e in json.loads(v)['result']['sources']]
    c.parse_research_manifest(json.dumps(manifest),evidence,c.recent_topics(ledger))
    return request,manifest,restored

def recover(raw):
    if c.p.now().date().isoformat()!=DAY or c.os.environ.get('GITHUB_REF')!='refs/heads/main':raise ValueError('Recovery date/branch rejected')
    if c.os.environ.get('PUBLIC_UPLOADS_ENABLED')!='true':raise ValueError('Existing publication gate required')
    ledger,sha=c.journal_get();request,manifest,restored=verify_inputs(raw,ledger)
    root=c.p.ROOT/'runs'/DAY
    if (root/'checkpoints').exists():raise ValueError('Clean recovery directory required')
    c.preflight() # read-only models, voice, included-credit reserve and exact channel
    rec=ledger['days'][DAY];original=copy.deepcopy(rec)
    for stage,raw_checkpoint in restored.items():
        path=root/'checkpoints'/(stage+'.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw_checkpoint)
    c.p.write(root/'recovery'/'original-record.json',original)
    rec['draft_429_recovery']={'source_run':RUN,'artifact_id':ARTIFACT,'archive_sha256':ARCHIVE_HASH,'previous_record':original,'original_result_and_billing':'unknown; observed HTTP429; no completed draft artifact','maximum_extra_draft_requests':1,'maximum_draft_output_tokens':7500,'at':c.p.now().isoformat()}
    # Archive exact failed entry rather than erase history. CAS commits before retry.
    del rec['checkpoints']['draft']
    rec.update(status='draft_recovery_ready')
    if c.p.now().date().isoformat()!=DAY:raise ValueError('Date changed before CAS')
    sha=c.journal_put(ledger,sha)
    (draft,_),sha=c.stage_checkpoint(ledger,sha,DAY,'draft',request=request,submit=lambda:c.openai(request['prompt'],False,7500,instructions=request['instructions'],stage='draft'))
    state={'sha':sha}
    def correction(*args,**kwargs):
        result,state['sha']=c.stage_checkpoint(ledger,state['sha'],DAY,'narration_correction',request={'args':args,'kwargs':kwargs},submit=lambda:c.openai(*args,**kwargs))
        return result
    jobs=c.guarded_draft_validation(ledger,lambda:state['sha'],DAY,draft,c.recent_topics(ledger),allow_length_correction=True,correction_provider=correction,source_manifest=manifest,active_only=True)
    sha=c.review_jobs(ledger,state['sha'],DAY,jobs) # unchanged rights/editorial guards
    c.publish_reviewed(ledger,sha,DAY) # unchanged duplicate, channel, TTS and upload guards

if __name__=='__main__':
    recover(download_archive(source_run=RUN,source_head=HEAD,artifact_id=ARTIFACT,archive_sha256=ARCHIVE_HASH,archive_size=43941))
