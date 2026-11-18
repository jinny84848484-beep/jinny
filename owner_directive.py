"""Owner (CEO) priority-topic directive for the daily Ojirap run.

Owner order 2026-11-19: when a work order was issued the day before, the daily
run follows it first. The order lives in the repository as
directives/ojirap/<YYYY-MM-DD>.json, named by the day it applies to.

A directive changes WHICH topics are researched first. It never relaxes a fact,
source, readiness, approval-scope or media rule: every directive topic is
researched and verified like any other candidate, and a slot whose directive
topic cannot be made ready is filled by the normal automatic selection.
A malformed directive is ignored (logged), never a reason to hold the day.

Example:
{
  "day": "2026-11-19",
  "issued_by": "CEO",
  "issued_at": "2026-11-19T00:10:00+09:00",
  "topics": [
    {"title": "추경호 대구시장 1심 무죄",
     "angle": "확인된 사실 + 질문: 1심은 확정이 아니다",
     "facts": ["서울중앙지법 형사합의34부, 내란중요임무종사 혐의 무죄", "특검 구형 징역 20년"],
     "sources": ["https://www.fnnews.com/news/202611181452418552"],
     "hook": "구형은 징역 20년, 선고는 무죄",
     "avoid": ["재판장 과거 판결 언급", "유죄 암시"]}
  ]
}
"""
from __future__ import annotations
import hashlib
import json
import re
import sys
import pipeline as p

MAX_TOPICS = 2
MAX_BYTES = 20000
DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
LIMITS = {'title': 120, 'angle': 300, 'hook': 120}
LIST_LIMITS = {'facts': (8, 300), 'sources': (6, 500), 'avoid': (6, 200)}


def path_for(day):
    if not DAY.match(str(day)):
        raise ValueError('Directive day must be YYYY-MM-DD')
    return p.ROOT / 'directives' / 'ojirap' / (day + '.json')


def _text(value, limit, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Directive ' + field + ' must be non-empty text')
    value = ' '.join(value.split())
    if len(value) > limit:
        raise ValueError('Directive ' + field + ' is too long')
    if any(ord(c) < 32 for c in value):
        raise ValueError('Directive ' + field + ' has control characters')
    return value


def validate(data, day):
    if not isinstance(data, dict) or data.get('day') != day:
        raise ValueError('Directive day does not match the run day')
    topics = data.get('topics')
    if not isinstance(topics, list) or not 1 <= len(topics) <= MAX_TOPICS:
        raise ValueError('Directive needs 1 or 2 topics')
    clean = []
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError('Directive topic must be an object')
        item = {'title': _text(topic.get('title'), LIMITS['title'], 'title')}
        for key in ('angle', 'hook'):
            if topic.get(key) is not None:
                item[key] = _text(topic[key], LIMITS[key], key)
        for key, (count, limit) in LIST_LIMITS.items():
            values = topic.get(key, [])
            if not isinstance(values, list) or len(values) > count:
                raise ValueError('Directive ' + key + ' must be a short list')
            item[key] = [_text(v, limit, key) for v in values]
        for url in item['sources']:
            if not url.startswith('https://') or ' ' in url:
                raise ValueError('Directive sources must be https URLs')
        clean.append(item)
    return {'day': day, 'issued_by': str(data.get('issued_by', ''))[:40],
            'issued_at': str(data.get('issued_at', ''))[:40], 'topics': clean}


def load(day):
    """Return (directive, sha256) for the run day, or (None, None)."""
    path = path_for(day)
    if not path.is_file() or path.is_symlink():
        return None, None
    raw = path.read_bytes()
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError('Directive file is too large')
        directive = validate(json.loads(raw.decode('utf-8')), day)
    except (ValueError, UnicodeDecodeError) as exc:
        print('Owner directive ignored: ' + str(exc)[:160], file=sys.stderr, flush=True)
        return None, None
    return directive, hashlib.sha256(raw).hexdigest()


def research_clause(directive):
    if not directive:
        return ''
    return ('\nOWNER DIRECTIVE (issued the previous day; follow it FIRST): research the following '
            + str(len(directive['topics'])) + ' topic(s) before any trend candidate and use each as one '
            'of today\'s two stories when it can be made ready. The listed facts and URLs are leads from '
            'the owner, not verified evidence: open and read the sources yourself, verify every fact '
            'against at least two independently operated sources exactly as the rules require, and '
            'correct or drop any listed fact you cannot verify. Never relax a source, readiness, '
            'approval-scope or media rule for a directive topic. If a directive topic still cannot be '
            'made ready, replace only that slot with the best normal candidate. Respect each topic\'s '
            '"avoid" list. Directive topics are exempt from the recent-topic exclusion only if they are '
            'a new development of that topic.\nDIRECTIVE TOPICS:\n'
            + json.dumps(directive['topics'], ensure_ascii=False))


def draft_clause(directive):
    if not directive:
        return ''
    guide = [{k: t[k] for k in ('title', 'angle', 'hook', 'avoid') if t.get(k)} for t in directive['topics']]
    return ('\nOWNER DIRECTIVE STYLE NOTES for any job that covers a directive topic: use the suggested '
            'angle and opening hook only when every word stays true to the verified packet, and never '
            'include anything on the avoid list. Facts still come ONLY from the research manifest.\n'
            + json.dumps(guide, ensure_ascii=False))
