"""Fail-closed one-run staging. Imported only by explicit stage_produce mode.

No plaintext checkpoint, private-key file, credential export or automatic retry.
Public keys/ciphertext are public; reviewed publication-ready jobs follow the
existing journal behavior. All review/render/upload guards remain shared.
"""
import copy
import base64
import hashlib
import json
import os
import re
import time

import cloud_runner as c
from staged_transfer import MAX_BYTES, canonical, create_receiver

REPOSITORY = 'jinny84848484-beep/jinny'
START_STATES = {'draft_validation_failed', 'research_completed_unusable'}


def run_context():
    if (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_ACTOR') != 'jinny84848484-beep'
            or os.environ.get('GITHUB_ACTOR_ID') != '341766743'
            or os.environ.get('GITHUB_TRIGGERING_ACTOR') != 'jinny84848484-beep'
            or os.environ.get('PUBLIC_UPLOADS_ENABLED') != 'true'):
        raise RuntimeError('Staged execution context not authorized')
    run = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    if not re.fullmatch(r'[0-9]{1,30}', run) or not re.fullmatch(r'[0-9]{1,8}', attempt):
        raise RuntimeError('Invalid staged run identity')
    signing_key=os.environ.get('STAGED_SENDER_PUBLIC_KEY','')
    from staged_transfer import unb64
    unb64(signing_key,32)
    return run, attempt, signing_key


def session_record(announcement):
    return {k: announcement[k] for k in ('run_id', 'run_attempt', 'day', 'session_id')}


def assert_waiting(ledger, day, identity):
    record = ledger['days'].get(day, {})
    if (record.get('status') != 'staged_waiting' or record.get('retry_hold') is not True
            or record.get('staging') != identity or record.get('jobs')
            or c.p.now().date().isoformat() != day):
        raise RuntimeError('Staged journal changed; reconcile before retrying')
    return record


def reject_unknown_fields(jobs):
    job_fields={'id','channel','topic_key','category','title','description','sources','scenes','experiment','opening_text','visual_queries'}
    source_fields={'title','url'}
    scene_fields={'kind','headline','body','narration','source_indices'}
    for job in jobs:
        if not isinstance(job,dict) or set(job)-job_fields:raise ValueError('Unknown staged job field')
        for key in ('id','channel','topic_key','category','title','description'):
            if not isinstance(job.get(key),str):raise ValueError('Invalid staged text field')
        if 'opening_text' in job and not isinstance(job['opening_text'],str):raise ValueError('Invalid staged text field')
        if not isinstance(job.get('sources'),list) or not isinstance(job.get('scenes'),list):raise ValueError('Invalid staged schema')
        for source in job['sources']:
            if not isinstance(source,dict) or set(source)!=source_fields:raise ValueError('Unknown staged source field')
            if not all(isinstance(source[k],str) for k in source_fields):raise ValueError('Invalid staged source field')
        for scene in job['scenes']:
            if not isinstance(scene,dict) or set(scene)-scene_fields:raise ValueError('Unknown staged scene field')
            if not all(isinstance(scene.get(k),str) for k in ('kind','headline','body','narration')):raise ValueError('Invalid staged scene field')
            indices=scene.get('source_indices',[])
            if not isinstance(indices,list) or any(type(i) is not int for i in indices):raise ValueError('Invalid staged citation index')
        if 'experiment' in job:
            experiment=job['experiment']
            if not isinstance(experiment,dict) or set(experiment)-{'hook_type','topic_category'}:raise ValueError('Unknown staged experiment field')
            if not all(isinstance(v,str) for v in experiment.values()):raise ValueError('Invalid staged experiment field')


def validate_staged(jobs, day, ledger):
    # No correction, research, paid request, ledger write or media search here.
    if not isinstance(jobs, list):
        raise ValueError('Invalid staged schema')
    recent = {x.get('topic_key', '').lower() for x in ledger.get('publications', [])[-40:]}
    slots=tuple((job['channel'],job['category']) for job in jobs)
    reject_unknown_fields(jobs)
    # The legacy validator stamps provenance while checking. Keep those synthetic
    # stamps confined to a disposable copy; the reviewer sees unstamped input.
    c.validate_batch(copy.deepcopy(jobs), day, recent, slots=slots)
    for job in jobs:
        queries = job.get('visual_queries')
        if (not isinstance(queries, list) or len(queries) != 2
                or any(not isinstance(q, str) or not 3 <= len(q) <= 100 for q in queries)):
            raise ValueError('Specific visual search queries required')
        if not isinstance(job.get('description'), str) or not job['description'].strip():
            raise ValueError('Staged description required')
        if any(not isinstance(scene.get('body'), str) for scene in job['scenes']):
            raise ValueError('Staged scene body required')
    return slots


def fetch_envelope(path):
    # The repo and path are fixed by trusted run context, never supplied content.
    url = 'https://api.github.com/repos/' + REPOSITORY + '/contents/' + path + '?ref=main'
    try:
        result = c.http(url, headers=c.gh_headers())
    except RuntimeError as exc:
        if 'HTTP 404' in str(exc):
            return None
        raise RuntimeError('Staged envelope read failed') from None
    try:
        if (result.get('type') != 'file' or result.get('encoding') != 'base64'
                or not isinstance(result.get('size'), int) or not 0 < result['size'] <= 2 * MAX_BYTES):
            raise ValueError()
        data = base64.b64decode(result['content'], validate=False)
        if len(data) != result['size'] or len(data) > 2 * MAX_BYTES:
            raise ValueError()
        # Pin exact bytes to the immutable Git blob identity, not an arbitrary URL.
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if blob != result['sha']:
            raise ValueError()
        return data, blob
    except (ValueError, TypeError, KeyError):
        raise RuntimeError('Staged envelope metadata rejected') from None


def run_staged():
    run, attempt, signing_key = run_context()
    day = c.p.now().date().isoformat()
    ledger, sha = c.journal_get()
    record = ledger['days'].get(day, {})
    if (record.get('status') not in START_STATES or record.get('retry_hold') is not True
            or record.get('jobs') or record.get('staging')):
        raise RuntimeError('Staged start requires reconciled failed batch; no replay')
    receiver = create_receiver(REPOSITORY, run, attempt, day, signing_key)
    identity = session_record(receiver.announcement)
    path = 'state/staging/' + run + '-' + attempt + '.json'
    try:
        record.setdefault('staging_history', []).append({
            'previous_status': record['status'], **identity, 'at': c.p.now().isoformat()})
        record.update(status='staged_waiting', retry_hold=True, staging=identity)
        c.journal_put(ledger, sha)  # Failure leaves original hold; no key is exposed.
        print('Staged public session: ' + json.dumps(receiver.announcement, sort_keys=True), flush=True)
        print('Staged ciphertext path: ' + path, flush=True)
        while time.monotonic() <= receiver.deadline:
            incoming = fetch_envelope(path)
            if incoming is not None:
                envelope, blob = incoming
                jobs = receiver.receive(envelope)['jobs']
                ledger, sha = c.journal_get()
                assert_waiting(ledger, day, identity)
                validate_staged(jobs, day, ledger)
                # Read-only checks occur only after the input passes offline guards.
                c.preflight()
                # Re-read immediately before the durable one-shot review marker.
                ledger, sha = c.journal_get()
                record = assert_waiting(ledger, day, identity)
                slots=validate_staged(jobs, day, ledger)
                record.update(status='staged_review_submitted', retry_hold=True,
                              staging_envelope_blob=blob)
                sha = c.journal_put(ledger, sha)
                # Existing source evidence, independent factual, media/license,
                # channel, credit, narration, renderer and publication checks.
                sha = c.review_jobs(ledger, sha, day, jobs, slots=slots)
                c.publish_reviewed(ledger, sha, day, slots=slots)
                return
            time.sleep(15)
        raise RuntimeError('Staged input expired; hold remains')
    except Exception:
        # Preserve waiting/submission markers. Never overwrite an uncertain journal
        # result, expose plaintext errors, retry providers or release the hold.
        raise RuntimeError('Staged attempt stopped; hold remains, reconcile journal') from None
    finally:
        receiver.close()
