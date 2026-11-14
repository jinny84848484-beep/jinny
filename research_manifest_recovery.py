"""Single-use, pinned research-only recovery; never certifies editorial PASS.

The original provider checkpoints and hold diagnostic remain unchanged. A fresh
operator-observed manifest is separately identified, then normal draft, editorial,
media, channel, duplicate, spend and publication guards run without exceptions.
"""
import copy
import hashlib
import io
import json
import zipfile
import cloud_runner as c

DAY = '2026-11-14'
SOURCE_RUN = '43544491120'
SOURCE_HEAD = 'f954db8f7d1f26988dd30d317be7a0f10753d7ca'
REPOSITORY = 'jinny84848484-beep/jinny'
ARTIFACT_ID = 14093348101
ARCHIVE_SHA256 = 'ef7242b08f870dd1880c59b0bb6b7b7e908483c2259272a04c766be4e0bc2ce2'
API_BASE = 'https://api.github.com/repos/' + REPOSITORY
BUNDLE_PATH = c.p.ROOT / 'records/recovery/2026-11-14-research.json'
BUNDLE_SHA256 = 'ec076f46281b3ac098bb8dedbc5688304aef2dadfff23d92ffdc51c3d5e79b9a'
RECORD_HASH = 'eb0e49cc6f686513fc1060c8492f092ff925f916d131cc0345de7a81cdf25792'

def download_archive(*, source_run=SOURCE_RUN, source_head=SOURCE_HEAD, artifact_id=ARTIFACT_ID, archive_sha256=ARCHIVE_SHA256, archive_size=38905):
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
    run = api('/actions/runs/' + source_run)
    if (str(run.get('id')) != source_run or run.get('run_attempt') != 1
            or run.get('status') != 'completed' or run.get('conclusion') != 'failure'
            or run.get('head_sha') != source_head or run.get('head_branch') != 'main'
            or run.get('workflow_id') != 407256129 or run.get('path') != '.github/workflows/shorts.yml'
            or run.get('event') != 'workflow_dispatch'
            or run.get('head_repository',{}).get('full_name') != REPOSITORY
            or run.get('actor',{}).get('id') != 341766743):
        raise ValueError('Recovery source run rejected')
    artifact = api('/actions/artifacts/' + str(artifact_id))
    if (artifact.get('id') != artifact_id or artifact.get('expired') is not False
            or artifact.get('name') != 'shorts-' + source_run
            or artifact.get('workflow_run',{}).get('id') != int(source_run)
            or artifact.get('workflow_run',{}).get('head_sha') != source_head
            or artifact.get('size_in_bytes') != archive_size
            or artifact.get('digest') != 'sha256:' + archive_sha256):
        raise ValueError('Recovery source artifact rejected')
    request = urllib.request.Request(API_BASE + '/actions/artifacts/' + str(artifact_id) + '/zip', headers=c.gh_headers())
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
    if len(raw) > 5_000_000 or hashlib.sha256(raw).hexdigest() != archive_sha256:
        raise ValueError('Recovery archive identity mismatch')
    return raw


def load_bundle():
    raw = BUNDLE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError('Research recovery evidence hash mismatch')
    bundle = json.loads(raw)
    if bundle['day'] != DAY or bundle['source_run'] != SOURCE_RUN:
        raise ValueError('Research recovery identity mismatch')
    return bundle


def recovered_manifest(record, recent):
    marker = record.get('research_recovery', {})
    if marker.get('bundle_sha256') != BUNDLE_SHA256 or marker.get('source_run') != SOURCE_RUN:
        raise ValueError('Research recovery marker mismatch')
    bundle = load_bundle()
    return c.parse_research_manifest(json.dumps(bundle['manifest']), bundle['evidence'], recent)


def write_source_packets(jobs):
    bundle = load_bundle()
    for job in jobs:
        urls = {source['url'] for source in job['sources']}
        entries = [entry for entry in bundle['evidence'] if entry['url'] in urls]
        if {entry['url'] for entry in entries} != urls:
            raise ValueError('Research recovery source binding mismatch')
        c.p.write(c.p.ROOT/'runs'/DAY/'sources'/(job['id']+'.json'), entries)


def recover_from_archive(raw):
    if c.p.now().date().isoformat() != DAY:
        raise ValueError('Research recovery is restricted to its production date')
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Research recovery archive hash mismatch')
    ledger, sha = c.journal_get()
    record = ledger.get('days', {}).get(DAY, {})
    if c.fingerprint(record) != RECORD_HASH:
        raise ValueError('Research recovery requires the exact unrecovered hold record')
    if (record.get('status') != 'research_validation_failed' or record.get('retry_hold') is not True
            or record.get('jobs') or set(record.get('checkpoints', {})) != {'research', 'research_repair'}):
        raise ValueError('Research recovery requires research-only hold')
    if any(str(row.get('job_id', '')).startswith(DAY) or str(row.get('at', '')).startswith(DAY)
           for row in ledger.get('publications', [])):
        raise ValueError('Research recovery conflicts with an existing publication')
    c.assert_no_unresolved_additional(ledger)
    bundle = load_bundle()
    c.parse_research_manifest(json.dumps(bundle['manifest']), bundle['evidence'], c.recent_topics(ledger))
    # Read only the two ledger-authorized checkpoints. The old artifact also
    # contains test fixtures: never restore draft or editorial files from it.
    originals = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Duplicate archive members')
        for stage, entry in record['checkpoints'].items():
            saved_raw = archive.read(DAY+'/checkpoints/'+stage+'.json')
            if hashlib.sha256(saved_raw).hexdigest() != entry['sha256']:
                raise ValueError('Original research checkpoint hash mismatch')
            saved = json.loads(saved_raw)
            if (saved.get('day') != DAY or saved.get('stage') != stage
                    or saved.get('request_hash') != entry['request_hash']):
                raise ValueError('Original research checkpoint identity mismatch')
            originals[stage] = saved_raw
    # Refuse local contamination, including fixtures from a preceding test run.
    run_dir = c.p.ROOT/'runs'/DAY
    if (run_dir/'checkpoints').exists():
        raise ValueError('Recovery requires an isolated clean run directory')
    c.preflight()
    for stage, saved_raw in originals.items():
        target = run_dir/'recovery'/'originals'/(stage+'.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(saved_raw)
    c.p.write(run_dir/'recovery'/'original-record.json', record)
    c.p.write(run_dir/'recovery'/'replacement-evidence.json', bundle)
    record['research_recovery'] = {
        'bundle_sha256': BUNDLE_SHA256, 'source_run': SOURCE_RUN,
        'source_head': SOURCE_HEAD, 'artifact_id': ARTIFACT_ID,
        'archive_sha256': ARCHIVE_SHA256, 'previous_record_hash': RECORD_HASH,
        'previous_record': copy.deepcopy(record), 'at': c.p.now().isoformat(),
        'method': 'operator_observed_research_only',
    }
    record.update(status='research_recovered', retry_hold=False)
    c.journal_put(ledger, sha)  # Compare-and-swap before any paid downstream work.
    c.produce()


if __name__ == '__main__':
    recover_from_archive(download_archive())
