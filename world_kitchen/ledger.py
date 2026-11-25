"""World Kitchen ledger: world_kitchen/ledger.json in the repository (one row per KST day).
Written only by the daily run through the GitHub contents API; never edited by hand.
Each published episode carries the completion code WK-YYYYMMDD in its description.
"""
from __future__ import annotations

import base64
import json
import os

from .providers import http

LEDGER = 'world_kitchen/ledger.json'


def code_for(day):
    return 'WK-' + day.replace('-', '')


def gh_headers():
    return {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'}


def read_ledger():
    url = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/' + LEDGER + '?ref=main'
    try:
        found = http(url, headers=gh_headers())
    except RuntimeError as exc:
        if 'HTTP 404' in str(exc):
            return {}, None
        raise
    return json.loads(base64.b64decode(found['content']).decode()), found['sha']


def write_ledger(ledger, sha, day):
    body = {'message': 'Record World Kitchen ' + day + ' [skip ci]',
            'content': base64.b64encode(json.dumps(ledger, ensure_ascii=False, indent=2).encode()).decode()}
    if sha:
        body['sha'] = sha
    result = http('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/' + LEDGER,
                  body, gh_headers(), 'PUT')
    return result['content']['sha']


def recent_dishes(ledger, limit=30):
    return [v.get('dish') for k, v in sorted(ledger.items()) if isinstance(v, dict) and v.get('dish')][-limit:]
