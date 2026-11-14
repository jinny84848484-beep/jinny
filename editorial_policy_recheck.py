"""One pinned re-audit of a reviewer-invented categorical approval rule.

Never turn FAIL into PASS. Keep the original decisions and re-audit the unchanged
second script once against the actual scope rule. All other checks remain active.
"""
import copy
import hashlib
import io
import json
import zipfile
import cloud_runner as c
import research_manifest_recovery as r

SOURCE_RUN = '43545735451'
SOURCE_HEAD = 'afd5525b5769f8e00251818520cd6a2ba6314ffe'
ARTIFACT_ID = 14093473524
ARCHIVE_SHA256 = 'dc04e1491806f13b891ebdf899b1880ed74ab2cc98bc57d0f5bb639ceac6cc1a'
RECORD_HASH = 'acb8fdfd680f50dd408a7a60d60872a3407f71ea194a37d80f524e6fe64ea23d'

CLARIFICATION = """Scope calibration: Apply DAILY_PUBLICATION_SCOPE exactly as supplied. The politics category, naming a current president, or reporting an official public diplomatic itinerary does not by itself establish a high-impact allegation or sensitive personal disclosure. Neutral discussion of announced public schedules, stated meeting agendas and the distinction between plans and accomplished results falls within routine public commentary. This is not an instruction to PASS: independently recheck this entire unchanged script, facts, opinion, media, naturalness and citations. Return approval_required for any actual serious allegation, sensitive personal disclosure or other specifically identified consequential claim outside the stated scope; explain that claim precisely. Do not invent a blanket human-approval rule for all political/diplomatic public information. The original safety and editorial requirements remain in force."""


def recover_from_archive(raw):
    if c.p.now().date().isoformat() != r.DAY or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Editorial recheck date or archive mismatch')
    ledger,sha=c.journal_get();record=ledger.get('days',{}).get(r.DAY,{})
    if c.fingerprint(record)!=RECORD_HASH:
        raise ValueError('Editorial recheck requires exact unreconciled hold')
    expected=[{'code':'approval_required','job_index':1,'scene_index':-1}]
    if (record.get('status')!='editorial_review_failed' or record.get('retry_hold') is not True
            or record.get('editorial_diagnostic',{}).get('issues')!=expected or record.get('jobs')):
        raise ValueError('Editorial recheck scope mismatch')
    if any(str(row.get('job_id','')).startswith(r.DAY) or str(row.get('at','')).startswith(r.DAY)
           for row in ledger.get('publications',[])):
        raise ValueError('Editorial recheck publication conflict')
    c.assert_no_unresolved_additional(ledger)
    r.recovered_manifest(record,c.recent_topics(ledger))
    files={}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist())!=len(set(archive.namelist())):raise ValueError('Duplicate archive entries')
        for item in archive.infolist():
            if (item.is_dir() or not item.filename.startswith(r.DAY+'/') or '..' in item.filename.split('/')
                    or item.file_size>1_000_000 or item.filename.startswith('/')):
                raise ValueError('Unexpected recheck archive member')
            files[item.filename]=archive.read(item)
    for stage,entry in record['checkpoints'].items():
        folder='recovery/originals/' if stage in ('research','research_repair') else 'checkpoints/'
        payload=files[r.DAY+'/'+folder+stage+'.json']
        if hashlib.sha256(payload).hexdigest()!=entry['sha256']:raise ValueError('Recheck checkpoint hash mismatch')
        value=json.loads(payload)
        if value.get('day')!=r.DAY or value.get('stage')!=stage or value.get('request_hash')!=entry['request_hash']:
            raise ValueError('Recheck checkpoint identity mismatch')
    saved=json.loads(files[r.DAY+'/checkpoints/editorial-review-input.json'])
    if c.fingerprint(saved)!=record['review_input_hash']:raise ValueError('Recheck input hash mismatch')
    jobs=copy.deepcopy(saved['jobs']);c.p.assert_active_news_jobs(jobs)
    c.validate_batch(jobs,r.DAY,c.recent_topics(ledger))
    for index in range(2):
        result=json.loads(files[r.DAY+'/checkpoints/editorial_review_'+str(index)+'.json'])['result']
        decision=json.loads(result['text'])
        if index==0:
            if decision.get('status')!='PASS':raise ValueError('First review must already have passed')
        elif (decision.get('status')!='FAIL' or len(decision.get('issues',[]))!=1
              or decision['issues'][0].get('code')!='approval_required'):
            raise ValueError('Only the identified policy interpretation may be rechecked')
    run_dir=c.p.ROOT/'runs'/r.DAY
    if run_dir.exists():raise ValueError('Editorial recheck requires a clean production directory')
    c.preflight()
    for name,payload in files.items():
        path=c.p.ROOT/'runs'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    c.p.write(run_dir/'recovery'/'prior-editorial-record.json',record)
    c.p.write(run_dir/'recovery'/'prior-editorial-details.json',json.loads(files[r.DAY+'/checkpoints/editorial-details.json']))
    record['editorial_policy_recheck']={'source_run':SOURCE_RUN,'artifact_id':ARTIFACT_ID,
        'archive_sha256':ARCHIVE_SHA256,'previous_record_hash':RECORD_HASH,
        'previous_diagnostic':copy.deepcopy(record['editorial_diagnostic']),
        'clarification_sha256':hashlib.sha256(CLARIFICATION.encode()).hexdigest(),
        'at':c.p.now().isoformat(),'maximum_new_review_requests':1}
    sha=c.journal_put(ledger,sha)
    # Keep the hold until BOTH real decisions pass. The old first review is
    # loaded by its original request/hash; only the second gets one new stage.
    sha=c.review_jobs(ledger,sha,r.DAY,jobs,policy_recheck=True)
    c.publish_reviewed(ledger,sha,r.DAY)


if __name__=='__main__':
    recover_from_archive(r.download_archive(source_run=SOURCE_RUN,source_head=SOURCE_HEAD,
        artifact_id=ARTIFACT_ID,archive_sha256=ARCHIVE_SHA256,archive_size=41853))
