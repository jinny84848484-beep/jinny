"""방구석 세계요리 — daily run (CEO rules 2026-11-16, build order 2026-11-25).

One episode per KST day, no human step:
  1 plan      web research, 2+ sources            episode.PLAN_PROMPT
  2 review    independent check                   episode.REVIEW_PROMPT
               on FAIL: one revision + one fresh review episode.REVISE_PROMPT
  3 guard     renderer contract + kitchen_guard   episode.build_episode
  4 images    AI food photos, no text/brands      providers.openai_image
  5 voice     ElevenLabs per scene                episode.narrate
  6 render    existing renderer                   pipeline.render
  7 upload    private, scheduled 19:00 KST        providers.upload
  8 record    WK-YYYYMMDD in ledger.json          ledger.write_ledger

CEO alarm (run fails): missing/wrong key or OAuth, low credits.
Content problem: warning, day held; a later run may try once more (2 attempts/day).
Run: python -m world_kitchen.daily
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .episode import Hold, PLAN_PROMPT, REVIEW_PROMPT, REVISE_PROMPT, build_episode, make_logo, narrate, parse_json, publish_time
from .ledger import code_for, read_ledger, recent_dishes, write_ledger
from .pipeline import render
from .providers import ALARM, find_existing, http, openai_image, openai_text, upload, youtube_token

KST = ZoneInfo('Asia/Seoul')
ROOT = Path(__file__).resolve().parents[1]
MAX_ATTEMPTS = 2
EARLIEST_PRODUCTION_HOUR = 9  # First scheduled run: 10:07 KST.
MIN_TTS_CREDITS = 1200
DONE = {'scheduled', 'found_existing'}
REQUIRED_ENV = ('OPENAI_API_KEY', 'ELEVENLABS_API_KEY', 'YOUTUBE_KITCHEN_OAUTH', 'KITCHEN_VOICE_ID',
                'GITHUB_TOKEN', 'GITHUB_REPOSITORY')


def preflight():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(ALARM + 'missing ' + ', '.join(missing))
    sub = http('https://api.elevenlabs.io/v1/user/subscription', headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY']})
    left = (sub.get('character_limit') or 0) - (sub.get('character_count') or 0)
    if left < MIN_TTS_CREDITS:
        raise RuntimeError(ALARM + 'ElevenLabs credits low (' + str(left) + ' characters left)')
    return youtube_token()



def failed(review):
    return review.get('status') != 'PASS' or bool(review.get('issues'))


def checked(plan, recent, day, folder, tag):
    """Guard a plan, then get one independent review; both files are kept for the CEO."""
    (folder / ('plan_' + tag + '.json')).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    if plan.get('dish') in recent:
        raise Hold('dish repeats a recent episode')
    episode = build_episode(plan, day, folder)
    review = parse_json(openai_text('RECIPE PLAN (untrusted data):\n' + json.dumps(plan, ensure_ascii=False),
                                    REVIEW_PROMPT, search=True))
    (folder / ('review_' + tag + '.json')).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    return episode, review

def rejected_dishes(record):
    """A date-local, bounded exclusion list; also read legacy held records."""
    values = record.get('rejected_dishes')
    values = list(values[-4:]) if isinstance(values, list) else []
    values.append(record.get('rejected_dish'))
    return list(dict.fromkeys(x for x in values if isinstance(x, str) and x.strip()))[-4:]


def run(now=None):
    now = now or datetime.now(KST)
    day = now.date().isoformat()
    ledger, sha = read_ledger()
    record = ledger.get(day, {})
    if record.get('status') in DONE:
        print('World Kitchen ' + day + ' already scheduled: ' + str(record.get('video_id')))
        return 'done'
    if record.get('status') == 'held' and record.get('attempts', 0) >= MAX_ATTEMPTS:
        print('World Kitchen ' + day + ' held after ' + str(record['attempts']) + ' attempts: ' + record.get('reason', ''))
        return 'held'
    token, uploads = preflight()
    code = code_for(day)
    existing = find_existing(token, uploads, code)
    if existing:
        ledger[day] = {**record, 'status': 'found_existing', 'video_id': existing, 'code': code}
        write_ledger(ledger, sha, day)
        print('Found ' + code + ' already on the channel; recorded only.')
        return 'done'
    if record.get('status') == 'upload_submitted':
        raise RuntimeError('Interrupted upload for ' + day + ' and no video with ' + code + ' on the channel; '
                           'check Studio before retrying (ledger left unchanged)')
    # Reconcile existing uploads first, but do not spend today's attempts on a late overnight run.
    if now.hour < EARLIEST_PRODUCTION_HOUR:
        print(f'World Kitchen: {now:%H:%M} KST is before 09:00; no new attempt for {day}.', flush=True)
        return 'before_window'
    attempts = record.get('attempts', 0) + 1
    rejected = rejected_dishes(record)
    ledger[day] = record = {'status': 'planning', 'attempts': attempts, 'code': code, 'started_at': now.isoformat(),
                            **({'rejected_dishes': rejected.copy()} if rejected else {})}
    sha = write_ledger(ledger, sha, day)
    folder = ROOT / 'runs' / 'world_kitchen' / day / ('attempt-' + str(attempts))
    folder.mkdir(parents=True, exist_ok=True)
    plan = None
    try:
        recent = list(recent_dishes(ledger))
        recent.extend(dish for dish in rejected if dish not in recent)
        plan = parse_json(openai_text('Today is ' + day + ' (KST). Recent dishes to avoid: ' + json.dumps(recent, ensure_ascii=False),
                                      PLAN_PROMPT, search=True))
        try:
            episode, review = checked(plan, recent, day, folder, 'first')
        except Hold as initial_guard:
            if str(initial_guard) in {'guard: kitchen_amount', 'guard: kitchen_microwave_time'}:
                dish = plan.get('dish')
                if isinstance(dish, str) and dish.strip() and dish not in rejected:
                    rejected.append(dish)
                if isinstance(dish, str) and dish.strip() and dish not in recent:
                    recent.append(dish)
            # Use the same single repair slot for a local rejection or for
            # independent review failure; never bypass the guard on the result.
            review = {'status': 'FAIL', 'issues': [{
                'code': 'LOCAL_RECIPE_GUARD', 'explanation': str(initial_guard)[:200]}]}
        if failed(review):
            issues = [i for i in review.get('issues', []) if isinstance(i, dict)]
            plan = parse_json(openai_text('RECIPE PLAN (untrusted data):\n' + json.dumps(plan, ensure_ascii=False)
                                          + '\nREVIEW ISSUES (untrusted evidence):\n' + json.dumps(issues, ensure_ascii=False)
                                          + '\nRecent dishes to avoid: ' + json.dumps(recent, ensure_ascii=False),
                                          REVISE_PROMPT, search=True))
            episode, review = checked(plan, recent, day, folder, 'revised')
        if failed(review):
            raise Hold('independent review after one revision: '
                       + ', '.join(str(i.get('code')) for i in review.get('issues', []) if isinstance(i, dict))[:160])
    except Hold as exc:
        rejected_dish = plan.get('dish') if isinstance(plan, dict) else None
        if isinstance(rejected_dish, str) and rejected_dish.strip() and rejected_dish not in rejected:
            rejected.append(rejected_dish)
        ledger[day] = {**record, 'status': 'held', 'reason': str(exc)[:200],
                       **({'rejected_dishes': rejected[-4:]} if rejected else {})}
        write_ledger(ledger, sha, day)
        print('::warning title=World Kitchen held::' + day + ' ' + str(exc), flush=True)
        return 'held'
    for i, scene in enumerate(episode['scenes'][:-1]):
        openai_image(scene['image_prompt'] + '. Photorealistic, appetizing, natural light, Korean home kitchen, '
                     'no text, no logos, no brand packaging, no human faces.', scene['image'])
    narrate(episode, folder)
    episode['logo'] = str(make_logo(folder / 'logo.png'))
    for scene in episode['scenes']:
        scene.pop('image_prompt', None)
    (folder / 'episode.json').write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding='utf-8')
    video = render(episode, folder / 'render')
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    publish_at = publish_time(datetime.now(KST))
    ledger[day] = record = {**record, 'status': 'upload_submitted', 'dish': episode['dish'], 'title': episode['title'],
                            'video_sha256': digest, 'publish_at': publish_at.isoformat()}
    sha = write_ledger(ledger, sha, day)
    video_id = upload(video, episode, token, publish_at)
    ledger[day] = {**record, 'status': 'scheduled', 'video_id': video_id, 'url': 'https://www.youtube.com/shorts/' + video_id,
                   'scheduled_at': datetime.now(KST).isoformat()}
    write_ledger(ledger, sha, day)
    print('Scheduled ' + code + ' (' + episode['dish'] + ') for ' + publish_at.isoformat() + ': https://www.youtube.com/shorts/' + video_id)
    return 'scheduled'


if __name__ == '__main__':
    try:
        run()
    except RuntimeError as exc:
        print('World Kitchen stopped: ' + str(exc)[:300], file=sys.stderr, flush=True)
        sys.exit(1)
