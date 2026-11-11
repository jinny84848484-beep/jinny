"""Explicit, read-only operational ledger check, separate from unit fixtures.

Usage: python ledger_health.py --ledger state/ledger.json
No production command, external API, credential access or journal write is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


HEALTHY = '운영 ledger 추가 배치 검사 통과'
UNRESOLVED = '운영 ledger에 미해결 배치 있음'
INVALID = '운영 ledger를 읽거나 검증할 수 없음'
CHANGED = '검사 중 운영 ledger가 변경됨'


def inspect_ledger(ledger, guard):
    """Inspect supplied data only; callers explicitly supply the production guard."""
    from additional_expiry import batch_resolved

    if not isinstance(ledger, dict) or not isinstance(ledger.get('additional_batches'), dict):
        return {'status': 'invalid', 'message': INVALID, 'guard_passed': False}
    batches = ledger['additional_batches']
    unresolved = 0
    for record in batches.values():
        try:
            resolved = batch_resolved(record)
        except (KeyError, TypeError, ValueError, AttributeError):
            resolved = False
        unresolved += not resolved
    try:
        guard(ledger)
        guard_passed = True
    except (RuntimeError, KeyError, TypeError, ValueError, AttributeError):
        guard_passed = False
    healthy = not unresolved and guard_passed
    return {
        'status': 'healthy' if healthy else 'unresolved',
        'message': HEALTHY if healthy else UNRESOLVED,
        'batch_count': len(batches),
        'unresolved_count': unresolved,
        'guard_passed': guard_passed,
    }


@contextmanager
def production_guard():
    # Importing pipeline normally prepares a state directory. Confine that
    # initialization to disposable storage, never the operational checkout.
    previous = os.environ.get('SHORTS_STATE_DIR')
    bytecode = sys.dont_write_bytecode
    with tempfile.TemporaryDirectory(prefix='shorts-ledger-check-') as folder:
        os.environ['SHORTS_STATE_DIR'] = folder
        sys.dont_write_bytecode = True
        try:
            from cloud_runner import assert_no_unresolved_additional
            yield assert_no_unresolved_additional
        finally:
            sys.dont_write_bytecode = bytecode
            if previous is None:
                os.environ.pop('SHORTS_STATE_DIR', None)
            else:
                os.environ['SHORTS_STATE_DIR'] = previous


def main(argv=None, *, guard=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True,
                        help='Explicit ledger file to read; no implicit live-ledger access')
    args = parser.parse_args(argv)
    try:
        raw = args.ledger.read_bytes()
        ledger = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        print(json.dumps({'status': 'invalid', 'message': INVALID}, ensure_ascii=False))
        return 2
    try:
        if guard is None:
            with production_guard() as actual_guard:
                report = inspect_ledger(ledger, actual_guard)
        else:
            report = inspect_ledger(ledger, guard)
        after = args.ledger.read_bytes()
    except (OSError, RuntimeError, KeyError, TypeError, ValueError, AttributeError):
        print(json.dumps({'status': 'invalid', 'message': INVALID}, ensure_ascii=False))
        return 2
    report['ledger_sha256'] = hashlib.sha256(raw).hexdigest()
    if after != raw:
        report.update(status='changed', message=CHANGED)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {'healthy': 0, 'unresolved': 1, 'invalid': 2, 'changed': 3}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
