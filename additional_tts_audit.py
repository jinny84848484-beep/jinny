"""Read-only ElevenLabs evidence collection. Never certifies unknown usage as zero.

Official docs (verified 2026-11-06):
https://elevenlabs.io/docs/api-reference/history/list
https://elevenlabs.io/docs/api-reference/analytics/workspace/usage
https://elevenlabs.io/docs/api-reference/user/subscription/get
https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode

History counters are not a documented billed-character contract. Analytics credit
units differ from input characters. No automatic production approval or hold clear.
"""
import datetime as dt
import json
import math
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
import additional_transport as transport

KST = dt.timezone(dt.timedelta(hours=9))
BASE = 'https://api.elevenlabs.io'
USAGE = '/v1/workspace/analytics/query/usage-by-product-over-time'
MAX_PAGES = 100
MAX_RESPONSE = 16 * 1024 * 1024


def day_window(day, now):
    date = dt.date.fromisoformat(day)
    if date.isoformat() != day or now.tzinfo is None or date > now.astimezone(KST).date():
        raise ValueError('Invalid audit date')
    start = dt.datetime.combine(date, dt.time(), KST)
    stop = start + dt.timedelta(days=1)
    end = min(stop, now)
    return int(start.timestamp()), int(end.timestamp()), end >= stop


def audit_request(method, path, query=None, body=None):
    # The one allowed POST is an analytics QUERY, not a generation/state-change API.
    if not ((method == 'GET' and path in {'/v1/history', '/v1/user/subscription'} and body is None)
            or (method == 'POST' and path == USAGE and query is None)):
        raise ValueError('Audit request not allowlisted')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    url = BASE + path + ('?' + urllib.parse.urlencode(query) if query else '')
    headers = {'xi-api-key': os.environ['ELEVENLABS_API_KEY']}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE: raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict): raise ValueError()
        return value
    except Exception:
        # Error bodies may contain customer/account/input text. Never retain them.
        raise RuntimeError('Read-only audit request unavailable or invalid') from None


def subscription_summary(value):
    result = {}
    for key in ('character_count', 'character_limit', 'next_character_count_reset_unix'):
        number = value.get(key)
        if type(number) is not int or number < 0: raise ValueError('Subscription counters unavailable')
        result[key] = number
    return result


def history_summary(request, start, end):
    seen = set(); cursors = set(); cursor = None; rows = []; complete = False
    for page in range(MAX_PAGES):
        query = {'date_after_unix': start, 'date_before_unix': end,
                 'page_size': 1000, 'sort_direction': 'asc', 'source': 'TTS'}
        if cursor: query['start_after_history_item_id'] = cursor
        value = request('GET', '/v1/history', query=query)
        items = value.get('history')
        if not isinstance(items, list) or type(value.get('has_more')) is not bool:
            raise ValueError('History schema unavailable')
        for item in items:
            identity = item.get('history_item_id'); stamp = item.get('date_unix')
            before = item.get('character_count_change_from'); after = item.get('character_count_change_to')
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError('History duplicate or missing identity')
            if type(stamp) is not int or not start <= stamp < end:
                raise ValueError('History timestamp outside requested window')
            if type(before) is not int or type(after) is not int or before < 0 or after < 0:
                raise ValueError('History counters unavailable')
            seen.add(identity)
            model = item.get('model_id')
            if not isinstance(model, str) or not re.fullmatch(r'eleven_[a-zA-Z0-9_\-]{1,60}', model):
                model = 'unknown'
            text = item.get('text')
            rows.append({'date_unix': stamp, 'model': model, 'history_counter_delta': after - before,
                         'history_text_codepoints': len(text) if isinstance(text, str) else None})
            # IDs, text, voice fields, request bodies and audio references are discarded.
        if not value['has_more']:
            complete = True; break
        cursor = value.get('last_history_item_id')
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError('History cursor missing or repeated')
        cursors.add(cursor)
    return {'pagination_complete': complete, 'pages': page + 1, 'records': rows,
            'record_count': len(rows), 'history_counter_delta_sum': sum(r['history_counter_delta'] for r in rows),
            'scope': 'direct TTS history; not proof of all account consumption',
            'billed_input_characters_verified': False}


def analytics_summary(value):
    """Retain safe numeric/timestamp/model fields, not arbitrary table content.

    Official rendering did not define exact column names/units/end inclusivity.
    Therefore this exposes sanitized evidence for review, never infers headroom.
    """
    columns = value.get('columns'); rows = value.get('rows'); units = value.get('column_units')
    if not isinstance(columns, list) or not isinstance(rows, list) or not isinstance(units, list) or len(units) != len(columns):
        return {'status': 'unsupported_schema', 'credits_verified': False}
    safe_names = {'time', 'timestamp', 'date', 'start_time', 'end_time', 'bucket_start', 'bucket_end',
                  'credits', 'credit_usage', 'usage', 'credit_count', 'character_count', 'model_id'}
    safe = [i for i, name in enumerate(columns) if isinstance(name, str) and name in safe_names]
    selected = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            return {'status': 'unsupported_rows', 'credits_verified': False}
        out = {}
        for i in safe:
            cell = row[i]; name = columns[i]
            if type(cell) in (int, float) and math.isfinite(cell): out[name] = cell
            elif name == 'model_id' and isinstance(cell, str) and re.fullmatch(r'eleven_[a-zA-Z0-9_\-]{1,60}', cell): out[name] = cell
            elif name in {'date', 'time', 'timestamp', 'start_time', 'end_time', 'bucket_start', 'bucket_end'} and isinstance(cell, str) and re.fullmatch(r'[0-9T:Z+\.\-]{10,40}', cell): out[name] = cell
        selected.append(out)
    return {'status': 'sanitized_evidence_only', 'row_count': len(rows), 'column_count': len(columns),
            'selected_columns': [columns[i] for i in safe],
            'selected_units': [units[i] if units[i] in ('credits', 'characters', 'milliseconds', 'seconds', None) else 'unrecognized' for i in safe],
            'rows': selected, 'credits_verified': False,
            'warning': 'Do not interpret totals until returned units, buckets, scope and end-time semantics are verified.'}


def collect(day, *, now=None, request=audit_request):
    now = now or dt.datetime.now(dt.timezone.utc)
    start, end, closed = day_window(day, now)
    result = {'mode': 'audit_only', 'production_date': day, 'time_zone': 'Asia/Seoul',
              'start_unix_seconds_inclusive': start, 'end_unix_seconds_exclusive_for_history': end,
              'day_closed': closed, 'coverage_label': 'closed day requested' if closed else 'through audit start time only',
              'includes_all_attempts_and_uncertain_submissions': False,
              'production_authorized': False, 'warnings': [
                  'History may omit deleted or zero-retention generations.',
                  'History counter deltas and text lengths are not authoritative billed credits.',
                  'Analytics finalization and exact schema/bucket semantics require independent verification.']}
    for key, action in (
        ('subscription_before', lambda: subscription_summary(request('GET', '/v1/user/subscription'))),
        ('history', lambda: history_summary(request, start, end)),
        ('analytics', lambda: analytics_summary(request('POST', USAGE, body={
            'start_time': start * 1000, 'end_time': end * 1000, 'interval_seconds': 86400, 'time_zone': 'Asia/Seoul'}))),
        ('subscription_after', lambda: subscription_summary(request('GET', '/v1/user/subscription')))):
        try: result[key] = action()
        except Exception: result[key] = {'status': 'unavailable_or_incomplete'}
    return result


def main():
    # No production imports, journal mutations, paid generation or upload functions.
    expected = {'GITHUB_REPOSITORY': 'jinny84848484-beep/jinny', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_ACTOR': 'jinny84848484-beep', 'GITHUB_ACTOR_ID': '341766743'}
    if any(os.environ.get(k) != v for k, v in expected.items()): raise ValueError('Audit context rejected')
    if not re.fullmatch('[0-9a-f]{40}', os.environ.get('AUDIT_EXPECTED_HEAD', '')) or os.environ['AUDIT_EXPECTED_HEAD'] != os.environ.get('GITHUB_SHA'):
        raise ValueError('Audit head mismatch')
    recipient = os.environ['AUDIT_RECOVERY_PUBLIC_KEY']; transport.validate_recovery_key(recipient)
    day = os.environ['AUDIT_PRODUCTION_DATE']; report = collect(day)
    with tempfile.TemporaryDirectory(prefix='additional-audit-') as temp:
        path = Path(temp) / 'sanitized-audit.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle: json.dump(report, handle, ensure_ascii=False, indent=2)
        transport.encrypt_recovery(temp, Path('private-artifacts/tts-audit.enc.json'), recipient,
            {'repository': expected['GITHUB_REPOSITORY'], 'run_id': os.environ['GITHUB_RUN_ID'],
             'run_attempt': '1', 'day': day, 'purpose_detail': 'read-only-tts-evidence'})
    print('Read-only audit evidence encrypted. Completeness remains unverified; production not started.')


if __name__ == '__main__':
    try: main()
    except Exception:
        print('Read-only audit unavailable; no production was started.')
        raise SystemExit(1)
