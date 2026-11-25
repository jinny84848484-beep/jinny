"""Weekly Instagram card news (@weekend.manual) — automatic carousel publishing.

CEO standing approval 2026-11-16: Instagram card news runs fully automatically.
CEO order 2026-11-25: make the card news upload automatically.

How it works (one issue per Friday, posted from 12:30 KST):
  1. The production lead (dot) commits the week's issue by Thursday 20:00 KST:
       instagram/issues/<YYYY-MM-DD>/issue.json + 01.jpg, 02.jpg, ...
  2. The chief of staff (Claude) may add instagram/issues/<day>/review.json
     ({"verdict": "PASS"|"FAIL", "exclude": ["03.jpg"], "note": "..."}).
     No review file = post on dot's own self check (no human wait).
  3. .github/workflows/instagram.yml runs this module. It validates the issue,
     checks the account, skips anything already posted, and publishes one
     carousel through the official Instagram API (Instagram Login).

issue.json example:
{
  "day": "2026-11-27",
  "self_check": "pass",
  "caption": "이번 주말 뭐 하지? ...\n\n출처: ...",
  "sources": ["https://...", "https://..."],
  "ai_generated": false,
  "cards": [{"file": "01.jpg", "alt": "표지: 이번 주말 갈 곳 4"}, ...]
}

Duplicate guard: every caption ends with the issue code (WM-YYYYMMDD). Before
publishing, the run reads the account's recent posts; if the code is already
there it only records the post. Posted issues are recorded in
instagram/posted.json (written by the workflow, never edited by hand).

CEO alarm only when the account/token is missing, expired or wrong
(see CONNECT_ERRORS) — everything else is handled or held automatically.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ISSUES = ROOT / 'instagram' / 'issues'
POSTED = 'instagram/posted.json'
KST = ZoneInfo('Asia/Seoul')
HANDLE = 'weekend.manual'
API = 'https://graph.instagram.com/v27.0'
DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
CARD = re.compile(r'^\d{2}\.jpe?g$')
MAX_CARDS = 20            # Meta: carousel up to 20 images/videos
MAX_BYTES = 8 * 1024 * 1024
MAX_CAPTION = 2200        # Meta: 2200 characters, 30 hashtags, 20 @ tags
MAX_TAGS = 30
MAX_MENTIONS = 20
RATIO = (0.8, 1.91)       # Meta: 4:5 to 1.91:1
POST_FROM = 12 * 60 + 30  # 12:30 KST on the issue day
POST_UNTIL_HOURS = 24     # until 12:30 KST the next day; later = stale, not posted
TOKEN_MAX_AGE_DAYS = 50   # long-lived token lasts 60 days; alarm 10 days early
CONNECT_ERRORS = 'CEO ALARM (Instagram connection): '


class Hold(Exception):
    """The issue is not posted this run; the reason is logged, nothing is sent."""


def code_for(day):
    return 'WM-' + day.replace('-', '')


# ---------- validation (offline) ----------

def jpeg_size(raw):
    """Return (width, height) of a baseline/progressive JPEG; reject MPO/other."""
    if raw[:3] != b'\xff\xd8\xff':
        raise Hold('not a JPEG')
    if b'MPF\x00' in raw[:65536]:
        raise Hold('extended JPEG (MPO) is not supported by Instagram')
    i = 2
    while i + 9 < len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(raw[i + 2:i + 4], 'big')
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(raw[i + 5:i + 7], 'big')
            w = int.from_bytes(raw[i + 7:i + 9], 'big')
            return w, h
        i += 2 + length
    raise Hold('JPEG size not found')


def load_issue(day, root=None):
    folder = (root or ISSUES) / day
    path = folder / 'issue.json'
    if not path.is_file():
        return None
    if path.is_symlink() or folder.is_symlink():
        raise Hold('issue path must not be a symlink')
    try:
        issue = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Hold('issue.json is not valid JSON: ' + str(exc)[:80])
    review = None
    if (folder / 'review.json').is_file():
        try:
            review = json.loads((folder / 'review.json').read_text(encoding='utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise Hold('review.json is not valid JSON')
    return validate(issue, review, day, folder)


def validate(issue, review, day, folder):
    if not isinstance(issue, dict) or issue.get('day') != day:
        raise Hold('issue day does not match today')
    if issue.get('self_check') != 'pass':
        raise Hold('production self check is not "pass"')
    if review is not None:
        if not isinstance(review, dict) or review.get('verdict') not in ('PASS', 'FAIL'):
            raise Hold('review.json verdict must be PASS or FAIL')
        if review['verdict'] == 'FAIL':
            raise Hold('review verdict FAIL: ' + str(review.get('note', ''))[:160])
    exclude = set(review.get('exclude', [])) if review else set()
    sources = issue.get('sources')
    if not isinstance(sources, list) or len(sources) < 2 or not all(
            isinstance(u, str) and u.startswith('https://') and ' ' not in u for u in sources):
        raise Hold('at least two https sources are required')
    caption = issue.get('caption')
    if not isinstance(caption, str) or not caption.strip():
        raise Hold('caption is empty')
    if '출처' not in caption:
        raise Hold('caption must carry a 출처 line')
    caption = caption.strip() + '\n\n' + code_for(day)
    if len(caption) > MAX_CAPTION:
        raise Hold('caption is longer than 2200 characters')
    if len(re.findall(r'#\w', caption)) > MAX_TAGS:
        raise Hold('more than 30 hashtags')
    if len(re.findall(r'@\w', caption)) > MAX_MENTIONS:
        raise Hold('more than 20 @ tags')
    cards = issue.get('cards')
    if not isinstance(cards, list) or not cards:
        raise Hold('no cards')
    kept, first_ratio = [], None
    for card in cards:
        if not isinstance(card, dict) or not CARD.match(str(card.get('file', ''))):
            raise Hold('card file names must look like 01.jpg')
        if card['file'] in exclude:
            continue
        path = folder / card['file']
        if not path.is_file() or path.is_symlink():
            raise Hold('missing card ' + card['file'])
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise Hold(card['file'] + ' is larger than 8 MB')
        w, h = jpeg_size(raw)
        ratio = w / h
        if not RATIO[0] - 0.005 <= ratio <= RATIO[1] + 0.005:
            raise Hold(card['file'] + ' aspect ratio must be between 4:5 and 1.91:1')
        if first_ratio is None:
            first_ratio = ratio
        elif abs(ratio - first_ratio) > 0.01:
            raise Hold(card['file'] + ' has a different shape from the first card (Instagram would crop it)')
        alt = str(card.get('alt', '')).strip()[:1000]
        kept.append({'file': card['file'], 'alt': alt})
    if len(kept) < 2:
        raise Hold('a carousel needs at least 2 cards after review exclusions')
    if len(kept) > MAX_CARDS:
        raise Hold('more than 20 cards')
    return {'day': day, 'code': code_for(day), 'caption': caption, 'cards': kept,
            'ai_generated': issue.get('ai_generated') is True, 'excluded': sorted(exclude)}


def in_window(day, now):
    start = datetime.fromisoformat(day).replace(tzinfo=KST) + timedelta(minutes=POST_FROM)
    return start <= now < start + timedelta(hours=POST_UNTIL_HOURS)


def token_age_check(issued_on, today):
    if not issued_on:
        raise RuntimeError(CONNECT_ERRORS + 'repository variable IG_TOKEN_ISSUED_ON (YYYY-MM-DD) is missing')
    age = (today - date.fromisoformat(issued_on)).days
    if age > TOKEN_MAX_AGE_DAYS:
        raise RuntimeError(CONNECT_ERRORS + 'access token is ' + str(age) + ' days old; issue a new one '
                           '(it expires at 60 days) and update IG_ACCESS_TOKEN and IG_TOKEN_ISSUED_ON')
    return age


# ---------- network ----------

def http(url, data=None, headers=None, method=None):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if not isinstance(data, bytes) else data
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:300]
        raise RuntimeError('HTTP ' + str(exc.code) + ' ' + url.split('?')[0] + ': ' + detail) from None


class Instagram:
    def __init__(self, token, user_id, sleep=time.sleep):
        self.auth = {'Authorization': 'Bearer ' + token}
        self.user = user_id
        self.sleep = sleep

    def get(self, path, **params):
        return http(API + path + ('?' + urllib.parse.urlencode(params) if params else ''), headers=self.auth)

    def post(self, path, **params):
        return http(API + path, params, self.auth, 'POST')

    def check_account(self):
        try:
            me = self.get('/me', fields='user_id,username')
        except RuntimeError as exc:
            raise RuntimeError(CONNECT_ERRORS + 'token rejected or expired — ' + str(exc)[:200]) from None
        if me.get('username') != HANDLE or str(me.get('user_id')) != str(self.user):
            raise RuntimeError(CONNECT_ERRORS + 'token belongs to @' + str(me.get('username')) + ', not @' + HANDLE)

    def already_posted(self, code):
        recent = self.get('/' + self.user + '/media', fields='id,caption,permalink,timestamp', limit='25')
        for item in recent.get('data', []):
            if code in (item.get('caption') or ''):
                return item
        return None

    def quota_left(self):
        info = self.get('/' + self.user + '/content_publishing_limit', fields='quota_usage,config')
        row = (info.get('data') or [{}])[0]
        return int(row.get('config', {}).get('quota_total', 50)) - int(row.get('quota_usage', 0))

    def wait(self, container):
        for _ in range(10):  # Meta: query status about once a minute, up to 5 minutes
            status = self.get('/' + container, fields='status_code').get('status_code')
            if status == 'FINISHED':
                return
            if status in ('ERROR', 'EXPIRED'):
                raise RuntimeError('container ' + container + ' ended as ' + status)
            self.sleep(30)
        raise RuntimeError('container ' + container + ' was not ready after 5 minutes')

    def publish(self, issue, base_url):
        children = []
        for card in issue['cards']:
            params = {'image_url': base_url + card['file'], 'is_carousel_item': 'true'}
            if card['alt']:
                params['alt_text'] = card['alt']
            children.append(self.post('/' + self.user + '/media', **params)['id'])
        for child in children:
            self.wait(child)
        params = {'media_type': 'CAROUSEL', 'children': ','.join(children), 'caption': issue['caption']}
        if issue['ai_generated']:
            params['is_ai_generated'] = 'true'
        carousel = self.post('/' + self.user + '/media', **params)['id']
        self.wait(carousel)
        media = self.post('/' + self.user + '/media_publish', creation_id=carousel)['id']
        info = self.get('/' + media, fields='permalink')
        return {'media_id': media, 'permalink': info.get('permalink', '')}


# ---------- record (GitHub contents API, like the shorts journal) ----------

def gh_headers():
    return {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'}


def read_posted():
    url = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/' + POSTED + '?ref=main'
    try:
        found = http(url, headers=gh_headers())
    except RuntimeError as exc:
        if 'HTTP 404' in str(exc):
            return {}, None
        raise
    return json.loads(base64.b64decode(found['content']).decode()), found['sha']


def write_posted(day, entry):
    posted, sha = read_posted()
    posted[day] = entry
    body = {'message': 'Record Instagram card news ' + day + ' [skip ci]',
            'content': base64.b64encode(json.dumps(posted, ensure_ascii=False, indent=2).encode()).decode()}
    if sha:
        body['sha'] = sha
    http('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/' + POSTED,
         json.dumps(body).encode(), {**gh_headers(), 'Content-Type': 'application/json'}, 'PUT')


# ---------- run ----------

def run(now=None, client=None):
    now = now or datetime.now(KST)
    day = now.date().isoformat()
    # An issue may still be posted the morning after its day (until 12:30 KST).
    candidates = [day, (now.date() - timedelta(days=1)).isoformat()]
    for candidate in candidates:
        if not in_window(candidate, now):
            continue
        posted, _ = read_posted()
        if candidate in posted:
            print('Already posted ' + candidate + ': ' + posted[candidate].get('permalink', ''))
            return 'done'
        try:
            issue = load_issue(candidate)
        except Hold as exc:
            # Not a CEO alarm: shown as a warning for the production lead and the code check.
            print('::warning title=Instagram card news held::' + candidate + ' ' + str(exc), flush=True)
            return 'hold'
        if issue is None:
            continue
        token_age_check(os.environ.get('IG_TOKEN_ISSUED_ON', ''), now.date())
        ig = client or Instagram(os.environ['IG_ACCESS_TOKEN'], os.environ['IG_USER_ID'])
        ig.check_account()
        existing = ig.already_posted(issue['code'])
        if existing:
            write_posted(candidate, {'media_id': existing['id'], 'permalink': existing.get('permalink', ''),
                                     'code': issue['code'], 'recorded': 'found_existing'})
            print('Found ' + issue['code'] + ' already on Instagram; recorded only.')
            return 'done'
        if ig.quota_left() < 1:
            print('HOLD ' + candidate + ': publishing quota used up; the next run retries.')
            return 'hold'
        base = ('https://raw.githubusercontent.com/' + os.environ['GITHUB_REPOSITORY'] + '/'
                + os.environ['GITHUB_SHA'] + '/instagram/issues/' + candidate + '/')
        result = ig.publish(issue, base)
        write_posted(candidate, {**result, 'code': issue['code'], 'cards': len(issue['cards']),
                                 'excluded': issue['excluded'], 'posted_at': datetime.now(KST).isoformat()})
        print('Published ' + issue['code'] + ' with ' + str(len(issue['cards'])) + ' cards: ' + result['permalink'])
        return 'posted'
    print('No Instagram issue due now.')
    return 'idle'


if __name__ == '__main__':
    try:
        run()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
