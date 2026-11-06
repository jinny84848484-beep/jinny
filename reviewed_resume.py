"""One-shot continuation of one pinned, already reviewed batch. No review API."""
import copy
import json
import os
import re
import urllib.request
import cloud_runner as c
from staged_runner import validate_staged

DAY='2026-11-06'
SOURCE_RUN='42136680107'
SOURCE_HEAD='1112626edfd2e3aa0980f6b8563c78978d4aa1e3'
REPO='jinny84848484-beep/jinny'
RECORD_HASH='7a549d1b2a3ab1ebb8f928734bc954ccffeea44ffee09fa4dae104af6b8fb5a9'
PUBLICATIONS_HASH='9e9f0b75a205ebb4c898f8b01230a2565f32ccd09737f944540453e0937fec22'
STAGES={'context','provenance','journal','preflight','ownership','publish_validation','download','job_files','tts_marker','render','render_marker','channel_verification','upload_marker','upload','publication_marker','completion_marker'}

def verify_context():
    expected={'GITHUB_REPOSITORY':REPO,'GITHUB_REF':'refs/heads/main',
              'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_ACTIONS':'true',
              'GITHUB_ACTOR':'jinny84848484-beep','GITHUB_ACTOR_ID':'341766743',
              'GITHUB_TRIGGERING_ACTOR':'jinny84848484-beep',
              'GITHUB_RUN_ATTEMPT':'1','PUBLIC_UPLOADS_ENABLED':'true'}
    if (any(os.environ.get(k)!=v for k,v in expected.items())
            or not os.environ.get('GITHUB_RUN_ID','').isdigit()
            or os.environ['GITHUB_RUN_ID'] in (SOURCE_RUN,'42130980896')
            or c.p.now().date().isoformat()!=DAY):
        raise ValueError('Resume context rejected')

def verify_source():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):return None
    opener=urllib.request.build_opener(NoRedirect)
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+'/actions/runs/'+SOURCE_RUN,headers=c.gh_headers())
    with opener.open(request,timeout=60) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError('Resume provenance rejected')
    value=json.loads(raw)
    if (str(value.get('id'))!=SOURCE_RUN or value.get('run_attempt')!=1
            or value.get('status')!='completed' or value.get('conclusion')!='failure'
            or value.get('head_sha')!=SOURCE_HEAD or value.get('head_branch')!='main'
            or value.get('workflow_id')!=407256129 or value.get('path')!='.github/workflows/shorts.yml'
            or value.get('event')!='workflow_dispatch'
            or value.get('head_repository',{}).get('full_name')!=REPO
            or value.get('actor',{}).get('id')!=341766743):
        raise ValueError('Resume provenance rejected')

def inspect_ledger(ledger):
    record=ledger.get('days',{}).get(DAY,{})
    if (c.p.now().date().isoformat()!=DAY or c.fingerprint(record)!=RECORD_HASH
            or c.fingerprint(ledger.get('publications'))!=PUBLICATIONS_HASH
            or record.get('status')!='reviewed' or record.get('retry_hold') is not True
            or record.get('cached_recovery',{}).get('run_id')!=SOURCE_RUN
            or any(k in record for k in ('active_job','reviewed_resume','upload_result','upload_results','video_id','rendered'))):
        raise ValueError('Resume journal rejected')
    jobs=copy.deepcopy(record['jobs']); media=record['media']
    # Revalidate supplied editorial decision and job structure without generating any decision.
    decision=c.parse_editorial_review(record['review'],jobs,media)
    if decision['status']!='PASS':raise ValueError('Resume review rejected')
    clean=copy.deepcopy(jobs)
    for job in clean:
        for key in ('verified_at','editorial_status','channel_id'):job.pop(key,None)
        for source in job['sources']:source.pop('checked_at',None)
        for scene in job['scenes']:scene.pop('media',None)
    slots=validate_staged(clean,DAY,ledger)
    c.validate_batch(copy.deepcopy(jobs),DAY,{x.get('topic_key','').lower() for x in ledger['publications'][-40:]},slots=slots)
    if any(j['id'] in {x.get('job_id') for x in ledger['publications']} for j in jobs):
        raise ValueError('Resume publication conflict')
    for job in jobs:
        approved=decision['selection'][job['id']]
        for index,scene in enumerate(job['scenes']):
            entry=media.get(scene.get('media'),{})
            if (scene.get('media')!=approved[index] or entry.get('relevance_verified') is not True
                    or entry.get('topic_key')!=job['topic_key']):
                raise ValueError('Resume media rejected')
    return slots

def run_resume():
    stage='context'
    def mark(name):
        nonlocal stage
        if name not in STAGES:raise ValueError('Invalid publication stage')
        stage=name
        # Fail before any new paid/public operation if the approved day changes.
        if name not in {'render_marker','publication_marker','completion_marker'} and c.p.now().date().isoformat()!=DAY:
            raise ValueError('Resume day expired')
        print('Reviewed resume stage: '+name,flush=True)
    try:
        verify_context()
        mark('provenance');verify_source()
        mark('journal');ledger,sha=c.journal_get();inspect_ledger(ledger)
        mark('preflight');c.preflight()
        # Do not trust the first read after preflight/network delays.
        mark('ownership');ledger,sha=c.journal_get();slots=inspect_ledger(ledger)
        ledger['days'][DAY]['reviewed_resume']={'source_run':SOURCE_RUN,'source_record_hash':RECORD_HASH,
            'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':'1','at':c.p.now().isoformat()}
        # Preserve original recovery, hold and entire previous history. CAS consumes once.
        sha=c.journal_put(ledger,sha)
        c.publish_reviewed(ledger,sha,DAY,slots=slots,stage_callback=mark)
    except Exception as exc:
        label=''
        if isinstance(exc,c.MediaDownloadError):
            value=str(exc)
            if re.fullmatch(r'Media download (HTTP [1-5][0-9]{2}|network failure)',value):label=' '+value
        raise RuntimeError('Reviewed resume stopped: stage='+stage+label+'; reconcile journal, no automatic retry') from None

if __name__=='__main__':run_resume()
