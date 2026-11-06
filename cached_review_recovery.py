"""Narrow one-shot recovery of a pinned, completed review artifact.

Transport is deliberately separate. The caller must obtain the repository-owned
artifact through an authorized download. Raw provider files remain unchanged.
"""
import copy
import hashlib
import io
import json
import os
import zipfile
import cloud_runner as c
from staged_runner import validate_staged

SOURCE_RUN = '42130980896'
SOURCE_ATTEMPT = '1'
DAY = '2026-11-06'
ARCHIVE_SHA256 = '1bbcb78ac5acae87b6099cd3eb8ae496548b0dcfe12fff434263311d1224f421'
INPUT_HASH = '7aebb669b5e674ceab3a788c7e7a3ae19f06a02a2df72f277dbdfb79c051a7f1'

def inspect_archive(raw, ledger, today):
    """Pure validation; no disk, journal, network or provider operations."""
    if today != DAY or len(raw) > 5_000_000 or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Recovery archive identity mismatch')
    record = ledger.get('days', {}).get(DAY, {})
    staging = record.get('staging', {})
    if (record.get('status') != 'editorial_review_failed' or record.get('retry_hold') is not True
            or record.get('review_input_hash') != INPUT_HASH
            or staging.get('run_id') != SOURCE_RUN or staging.get('run_attempt') != SOURCE_ATTEMPT
            or staging.get('day') != DAY or record.get('jobs')
            or any(k in record for k in ('active_job','upload_result','upload_results','video_id','rendered','cached_recovery'))):
        raise ValueError('Recovery journal not eligible')
    diagnostic = record.get('editorial_diagnostic', {})
    if (diagnostic.get('status') != 'FAIL' or diagnostic.get('input_hash') != INPUT_HASH
            or diagnostic.get('issues') != [{'code':'review_contract','job_index':2,'scene_index':-1}]):
        raise ValueError('Recovery requires exact recorded contract failure')
    cps = record.get('checkpoints', {})
    if any(not isinstance(v,dict) or v.get('status') != 'completed' for v in cps.values()):
        raise ValueError('Recovery checkpoint incomplete')
    names = ['editorial-review-input.json'] + [f'editorial_review_{i}.json' for i in range(4)]
    files = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or len({x.filename for x in entries}) != len(entries):
            raise ValueError('Recovery archive entries rejected')
        for name in names:
            path = DAY + '/checkpoints/' + name
            info = archive.getinfo(path)
            if info.file_size > 2_000_000 or info.is_dir():
                raise ValueError('Recovery archive entry rejected')
            files[name] = archive.read(info)
    source_input = json.loads(files[names[0]])
    if c.fingerprint(source_input) != INPUT_HASH:
        raise ValueError('Recovery input hash mismatch')
    jobs, media = source_input['jobs'], source_input['images']
    if len(jobs) != 4 or any(j['id'] in {p.get('job_id') for p in ledger.get('publications',[])} for j in jobs):
        raise ValueError('Recovery publication conflict')
    slots = validate_staged(copy.deepcopy(jobs), DAY, ledger)
    for i, job in enumerate(jobs):
        stage = f'editorial_review_{i}'
        cp = cps.get(stage, {})
        blob = files[stage + '.json']
        result = json.loads(blob)
        if (cp.get('usable') is not True or cp.get('attempts') != 1
                or hashlib.sha256(blob).hexdigest() != cp.get('sha256')
                or result.get('day') != DAY or result.get('stage') != stage
                or result.get('request_hash') != cp.get('request_hash')):
            raise ValueError('Recovery checkpoint identity mismatch')
        decision = c.parse_editorial_review(result['result']['text'], [job], media)
        if decision['status'] != 'PASS':
            raise ValueError('Recovery requires all reviews to pass')
    return files, jobs, slots

def recover_from_archive(raw):
    """Future authorized entry point. Never invokes a new editorial provider call."""
    expected = {'GITHUB_REPOSITORY':'jinny84848484-beep/jinny','GITHUB_REF':'refs/heads/main',
                'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_ACTIONS':'true',
                'GITHUB_ACTOR':'jinny84848484-beep','GITHUB_ACTOR_ID':'341766743',
                'GITHUB_TRIGGERING_ACTOR':'jinny84848484-beep','PUBLIC_UPLOADS_ENABLED':'true'}
    if (any(os.environ.get(k) != v for k,v in expected.items())
            or not os.environ.get('GITHUB_RUN_ID','').isdigit()
            or os.environ.get('GITHUB_RUN_ID') == SOURCE_RUN
            or os.environ.get('GITHUB_RUN_ATTEMPT') != '1'):
        raise RuntimeError('Recovery execution context rejected')
    ledger, sha = c.journal_get()
    files, jobs, slots = inspect_archive(raw, ledger, c.p.now().date().isoformat())
    # Restore only exact pinned checkpoint bytes, never extract arbitrary paths.
    folder = c.p.ROOT / 'runs' / DAY / 'checkpoints'
    folder.mkdir(parents=True, exist_ok=True)
    for name, blob in files.items():
        (folder / name).write_bytes(blob)
    c.preflight()
    # Re-read after preflight, preserve all history, acquire the one-shot owner.
    ledger, sha = c.journal_get()
    inspect_archive(raw, ledger, c.p.now().date().isoformat())
    record = ledger['days'][DAY]
    record['cached_recovery'] = {'source_run':SOURCE_RUN,'archive_sha256':ARCHIVE_SHA256,
        'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':'1','at':c.p.now().isoformat(),
        'previous_diagnostic':copy.deepcopy(record['editorial_diagnostic'])}
    record.update(status='cached_review_recovery_submitted', retry_hold=True)
    sha = c.journal_put(ledger, sha)
    sha = c.review_jobs(ledger, sha, DAY, jobs, slots=slots, cached_only=True)
    if c.p.now().date().isoformat() != DAY:
        raise ValueError('Recovery day changed; hold remains')
    c.publish_reviewed(ledger, sha, DAY, slots=slots)

REPOSITORY = 'jinny84848484-beep/jinny'
SOURCE_HEAD = '3e0ff61246bb49d200b456a2a3c65291cd4e48ad'
ARTIFACT_ID = 13464070657
API_BASE = 'https://api.github.com/repos/' + REPOSITORY

def download_archive():
    """Read fixed repository artifact. Never forward the token to its signed URL."""
    import urllib.request
    import urllib.parse
    import urllib.error
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    def api(path):
        req = urllib.request.Request(API_BASE + path, headers=c.gh_headers())
        with opener.open(req, timeout=60) as response:
            return json.loads(response.read(1_000_001))
    run = api('/actions/runs/' + SOURCE_RUN)
    if (str(run.get('id')) != SOURCE_RUN or run.get('run_attempt') != 1
            or run.get('status') != 'completed' or run.get('conclusion') != 'failure'
            or run.get('head_sha') != SOURCE_HEAD or run.get('head_branch') != 'main'
            or run.get('workflow_id') != 407256129 or run.get('path') != '.github/workflows/shorts.yml'
            or run.get('event') != 'workflow_dispatch'
            or run.get('head_repository',{}).get('full_name') != REPOSITORY
            or run.get('actor',{}).get('id') != 341766743):
        raise ValueError('Recovery source run rejected')
    artifact = api('/actions/artifacts/' + str(ARTIFACT_ID))
    if (artifact.get('id') != ARTIFACT_ID or artifact.get('expired') is not False
            or artifact.get('name') != 'shorts-' + SOURCE_RUN
            or artifact.get('workflow_run',{}).get('id') != int(SOURCE_RUN)
            or artifact.get('workflow_run',{}).get('head_sha') != SOURCE_HEAD
            or artifact.get('size_in_bytes') != 34012
            or artifact.get('digest') != 'sha256:' + ARCHIVE_SHA256):
        raise ValueError('Recovery source artifact rejected')
    request = urllib.request.Request(API_BASE + '/actions/artifacts/' + str(ARTIFACT_ID) + '/zip', headers=c.gh_headers())
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise RuntimeError('Recovery artifact download rejected') from None
        location = exc.headers.get('Location', '')
        parsed = urllib.parse.urlsplit(location)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443)
                or not parsed.hostname or not (parsed.hostname.endswith('.blob.core.windows.net')
                    or parsed.hostname.endswith('.actions.githubusercontent.com'))):
            raise ValueError('Recovery artifact redirect rejected') from None
        # A fresh unauthenticated request; redirects remain disabled.
        response = opener.open(urllib.request.Request(location), timeout=60)
    with response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000 or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Recovery archive identity mismatch')
    return raw

def run_recovery():
    try:
        recover_from_archive(download_archive())
    except Exception:
        # No provider errors, source text, signed URLs or credentials in logs.
        raise RuntimeError('Cached recovery stopped; reconcile journal, no automatic retry') from None

if __name__ == '__main__':
    run_recovery()
