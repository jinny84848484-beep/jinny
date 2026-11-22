"""One-shot recovery of the exact 2026-11-22 draft; real licensed photos only."""
import copy
import hashlib
import io
import json
import os
import re
from urllib import error, parse, request
from zipfile import ZipFile

import cloud_runner as c

DAY = '2026-11-22'
MODE = 'produce'
REPO = 'jinny84848484-beep/jinny'
CHANNEL_ID = 'UCZj1FlWDcoiEQNLfpwc5qtw'
RUN = 44968565375
RUN_HEAD = '4a21a664921475b0fc47dd7a9ba7aa49af320e23'
ARTIFACT = 14732861424
SIZE = 48551
ZIP_SHA = '84412ab3bf2e82eeb023ab8ef48737528a25f88afe489073ffaf9fd19f6ed35c'
CHECKPOINTS = {
    'research': '657f5224c38828316517597e6c9abb76d3a66121a7aa78dd571a04b9198498c0',
    'research_repair': '0c0a72ce66e181688c73df4d99e0ae8b872d19fd90e5f3dc76b3f70c35c4d075',
    'draft': '3ad7a21a1f420b3eed2bac329195d739d0150b9a3ab1cc9be43e40375898e871',
}
JOBS = ('2026-11-22-lguplus-ai-reorg', '2026-11-22-suneung-ai-crackdown')
PHOTO_TITLES = {
    JOBS[0]: {
        'File:LG Telecom Daerijeom.jpg': '자료사진 · 2008년 LG텔레콤 판매점 · 현 LG유플러스 본사 아님',
        'File:NOIRLab HQ Server Racks (6V6A0404-CC).jpg': '자료사진 · 2022년 미국 NOIRLab 서버실 · LG 시설 아님',
    },
    JOBS[1]: {
        'File:The exam location (Puhung High School), for The South Korea College Scholastic Ability Test, 2018.jpg':
            '자료사진 · 2017년 수능 시험장 · 이번 사건과 무관',
        'File:Puhung High School main gate during the South Korea College Scholastic Ability Test, 2018 (2).jpg':
            '자료사진 · 2017년 수능 시험장 정문 · 이번 사건과 무관',
    },
}
LICENSES = {'CC0', 'CC0 1.0', 'Public domain', 'CC BY 2.0', 'CC BY 2.5', 'CC BY 3.0', 'CC BY 4.0'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def download():
    """Only the first-party API gets credentials; signed storage gets none."""
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = request.build_opener(NoRedirect)
    api = 'https://api.github.com/repos/' + REPO
    def req(path):
        return request.Request(api + path, headers=c.gh_headers())
    def meta(path):
        with opener.open(req(path), timeout=40) as res:
            return json.loads(res.read(1_000_001))
    run = meta('/actions/runs/' + str(RUN))
    if (run.get('id') != RUN or run.get('head_sha') != RUN_HEAD or run.get('head_branch') != 'main'
            or run.get('run_attempt') != 1 or run.get('status') != 'completed'
            or run.get('conclusion') != 'failure'
            or (run.get('path') or '').split('@')[0] != '.github/workflows/shorts.yml'
            or (run.get('head_repository') or {}).get('full_name') != REPO):
        raise RuntimeError('Nov 22 source run identity mismatch')
    artifact_path = '/actions/artifacts/' + str(ARTIFACT)
    artifact = meta(artifact_path)
    wr = artifact.get('workflow_run') or {}
    if (artifact.get('id') != ARTIFACT or artifact.get('name') != 'shorts-' + str(RUN)
            or artifact.get('expired') is not False or artifact.get('size_in_bytes') != SIZE
            or artifact.get('digest') != 'sha256:' + ZIP_SHA
            or wr.get('id') != RUN or wr.get('head_sha') != RUN_HEAD):
        raise RuntimeError('Nov 22 source artifact identity mismatch')
    try:
        response = opener.open(req(artifact_path + '/zip'), timeout=40)
    except error.HTTPError as exc:
        if exc.code != 302:
            raise RuntimeError('Nov 22 source archive download rejected') from None
        url = exc.headers.get('Location', '')
        parsed = parse.urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not host.endswith(('.blob.core.windows.net', '.actions.githubusercontent.com'))):
            raise RuntimeError('Nov 22 source archive redirect rejected') from None
        response = opener.open(request.Request(url), timeout=40)
    with response:
        raw = response.read(SIZE + 1)
    if len(raw) != SIZE or sha(raw) != ZIP_SHA:
        raise RuntimeError('Nov 22 source archive checksum mismatch')
    return raw


def unpack(raw, record):
    if len(raw) != SIZE or sha(raw) != ZIP_SHA:
        raise RuntimeError('Nov 22 source archive checksum mismatch')
    values = {}
    with ZipFile(io.BytesIO(raw)) as z:
        if len(set(z.namelist())) != len(z.namelist()):
            raise RuntimeError('Nov 22 repeated archive entry')
        for stage, digest in CHECKPOINTS.items():
            path = DAY + '/checkpoints/' + stage + '.json'
            if z.getinfo(path).file_size > 100_000:
                raise RuntimeError('Nov 22 oversized checkpoint')
            data = z.read(path)
            item = json.loads(data)
            prior = record['checkpoints'][stage]
            if (sha(data) != digest or prior.get('sha256') != digest
                    or prior.get('status') != 'completed' or prior.get('usable') is not True
                    or prior.get('attempts') != 1
                    or item.get('day') != DAY or item.get('stage') != stage
                    or item.get('request_hash') != prior.get('request_hash')):
                raise RuntimeError('Nov 22 existing checkpoint identity mismatch')
            values[stage] = data
        path = DAY + '/checkpoints/editorial-review-input.json'
        if z.getinfo(path).file_size > 100_000:
            raise RuntimeError('Nov 22 oversized draft handoff')
        expected = json.loads(z.read(path))
        if expected.get('images') != {} or tuple(x.get('id') for x in expected.get('jobs', [])) != JOBS:
            raise RuntimeError('Nov 22 original review handoff shape differs')
    return values, expected['jobs']


def eligible(ledger):
    rec = ledger.get('days', {}).get(DAY, {})
    if (set(rec) != {'status', 'checkpoints', 'active_stage'}
            or rec['status'] != 'draft_completed' or rec['active_stage'] != 'draft'
            or set(rec['checkpoints']) != set(CHECKPOINTS)):
        raise RuntimeError('Nov 22 already continued or original ledger state changed')
    if any(item.get('channel') == 'ojirap' and (
            str(item.get('job_id', '')).startswith(DAY) or str(item.get('at', '')).startswith(DAY))
            for item in ledger.get('publications', [])):
        raise RuntimeError('Nov 22 already has an Ojirap publication')
    c.assert_no_unresolved_additional(ledger)
    return rec


def canonical_jobs(jobs):
    actual = copy.deepcopy(jobs)
    for job in actual:
        for k in ('verified_at', 'editorial_status', 'channel_id'):
            job.pop(k, None)
        for source in job['sources']:
            source.pop('checked_at', None)
        for scene in job['scenes']:
            scene.pop('media', None)
    return actual


def real_photo(key, asset, job):
    """Keep the existing Commons rights/identity gate; no graphics or synthetic substitutes."""
    url = parse.urlsplit(asset.get('url', ''))
    source = parse.urlsplit(asset.get('source', ''))
    return (re.fullmatch(r'commons-[0-9]+-[a-f0-9]{8}', key) is not None
            and asset.get('media_type') == 'image' and asset.get('license') in LICENSES
            and not asset.get('policy') and bool(asset.get('credit'))
            and str(asset.get('notice', '')).startswith('자료사진 · ')
            and asset.get('topic_key') == job['topic_key'] and asset.get('channel') == 'ojirap'
            and url.scheme == 'https' and url.hostname == 'upload.wikimedia.org'
            and source.scheme == 'https' and source.hostname == 'commons.wikimedia.org')


def recover(raw):
    if (c.p.now().date().isoformat() != DAY or os.getenv('RUN_MODE') != MODE
            or os.getenv('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.getenv('GITHUB_REPOSITORY') != REPO or os.getenv('GITHUB_REF') != 'refs/heads/ojirap-20261122-photo-recovery'
            or os.getenv('PUBLIC_UPLOADS_ENABLED') != 'true'
            or c.p.cfg()['channels']['ojirap']['channel_id'] != CHANNEL_ID):
        raise RuntimeError('Nov 22 recovery authorization or date does not match')
    ledger, _ = c.journal_get()
    values, expected = unpack(raw, eligible(ledger))
    c.p.assert_active_news_jobs(expected)
    day_dir = c.p.ROOT / 'runs' / DAY
    if day_dir.exists():
        raise RuntimeError('Nov 22 recovery requires an isolated run directory')
    output = day_dir / 'checkpoints'
    output.mkdir(parents=True)
    for stage, original in values.items():
        (output / (stage + '.json')).write_bytes(original)
    review_original = c.review_jobs
    provider_original = c.openai
    candidates_original = c.review_candidates
    graphics_original = c.daily_graphics.enabled
    publish_original = c.publish_reviewed
    phase = {'review': False, 'calls': 0, 'ready': False}

    def provider(*args, **kwargs):
        if not phase['review'] or kwargs.get('stage') != 'editorial_review' or phase['calls'] >= len(JOBS):
            raise RuntimeError('Nov 22 recovery forbids regeneration and extra reviewer calls')
        phase['calls'] += 1
        return provider_original(*args, **kwargs)

    def discover(jobs):
        found = {}
        for job in jobs:
            labels = PHOTO_TITLES[job['id']]
            own = c.relevant_media.resolve_titles(list(labels), job['topic_key'], job['channel'])
            if len(own) != 2 or {a.get('title') for a in own.values()} != set(labels):
                raise RuntimeError('Nov 22 has insufficient verified-license real-photo candidates')
            for key, asset in own.items():
                asset['notice'] = labels[asset['title']]
                if not real_photo(key, asset, job):
                    raise RuntimeError('Nov 22 resolved photo does not match the license and source guard')
            found.update(own)
        return found

    def review(journal, rev, day, jobs):
        rec = eligible(journal)
        if day != DAY or canonical_jobs(jobs) != expected:
            raise RuntimeError('Nov 22 reconstructed draft differs from source run')
        # Fail before reserving or contacting reviewers if the photo precheck fails.
        photos = discover(jobs)
        rec['nov22_photo_recovery'] = {'run': RUN, 'artifact': ARTIFACT, 'zip_sha256': ZIP_SHA}
        rev = c.journal_put(journal, rev)
        phase['review'] = True
        c.review_candidates = lambda jobs: photos
        c.daily_graphics.enabled = lambda: False
        try:
            rev = review_original(journal, rev, day, jobs)
        finally:
            phase['review'] = False
            c.review_candidates = candidates_original
            c.daily_graphics.enabled = graphics_original
        phase['ready'] = True
        return rev

    def publish(journal, rev, day):
        rec = journal['days'][DAY]
        jobs = rec.get('jobs') or []
        if (not phase['ready'] or phase['calls'] != len(JOBS) or day != DAY
                or rec.get('status') != 'reviewed'
                or tuple(j.get('id') for j in jobs) != JOBS
                or canonical_jobs(jobs) != expected):
            raise RuntimeError('Nov 22 clean full editorial review required')
        exclusions = rec.get('editorial_exclusions') or []
        excluded = {e['job_id'] for e in exclusions if e.get('reason') == 'editorial_review_failed'
                    and e.get('channel') == 'ojirap' and e.get('job_id') in JOBS}
        if len(excluded) != len(exclusions) or len(excluded) >= len(JOBS):
            raise RuntimeError('Nov 22 no independently passing story')
        kept = [job for job in jobs if job['id'] not in excluded]
        for job in kept:
            choices = [scene.get('media') for scene in job['scenes']]
            if job.get('channel_id') != CHANNEL_ID or len(set(choices)) < 2:
                raise RuntimeError('Nov 22 channel or photo diversity mismatch')
            for key in choices:
                asset = rec['media'].get(key, {}) if isinstance(key, str) else {}
                if not isinstance(key, str) or not asset.get('relevance_verified') or not real_photo(key, asset, job):
                    raise RuntimeError('Nov 22 selected media was not a reviewed real photo')
        return publish_original(journal, rev, day)

    c.review_jobs = review
    c.openai = provider
    c.publish_reviewed = publish
    try:
        c.produce(expected_day=DAY)
        if not phase['ready']:
            raise RuntimeError('Nov 22 recovery did not reach editorial review')
    finally:
        c.review_jobs = review_original
        c.openai = provider_original
        c.publish_reviewed = publish_original


if __name__ == '__main__':
    recover(download())
