"""Manual one-run encrypted transport; never invoked by the daily schedule."""
import copy
import json
import os
import re
import time
from pathlib import Path
import additional_batch as a
import additional_runtime as ar
import additional_transport as at
import cloud_runner as c
import staged_transfer as t
import staged_runner as staged


def dispatch_context():
    run, attempt, signing_key = staged.run_context()
    a.require(attempt == '1', 'Additional transport cannot rerun an old attempt')
    values = {name: os.environ.get(name, '') for name in
              ('ADDITIONAL_BATCH_ID', 'ADDITIONAL_PRODUCTION_DATE', 'ADDITIONAL_ANCHOR_DAY', 'ADDITIONAL_APPROVAL_HASH',
               'ADDITIONAL_APPROVED_HEAD', 'ADDITIONAL_RECOVERY_PUBLIC_KEY')}
    day = values['ADDITIONAL_PRODUCTION_DATE']; batch = values['ADDITIONAL_BATCH_ID']
    anchor = values['ADDITIONAL_ANCHOR_DAY'] or day
    a.require(a.dt.date.fromisoformat(anchor).isoformat() == anchor and anchor <= day, 'Invalid dispatch anchor date')
    values['ADDITIONAL_ANCHOR_DAY'] = anchor
    a.require(day == c.p.now().date().isoformat() and
              re.fullmatch(re.escape(day) + '-extra-[0-9]{2}', batch) is not None,
              'Additional dispatch day or identity invalid')
    a.require(re.fullmatch('[0-9a-f]{64}', values['ADDITIONAL_APPROVAL_HASH']) is not None and
              re.fullmatch('[0-9a-f]{40}', values['ADDITIONAL_APPROVED_HEAD']) is not None and
              values['ADDITIONAL_APPROVED_HEAD'] == os.environ.get('GITHUB_SHA'),
              'Additional dispatch approval or head invalid')
    at.validate_recovery_key(values['ADDITIONAL_RECOVERY_PUBLIC_KEY'])
    return run, attempt, signing_key, values


def register_session(ledger, run, identity, day, batch, approval_hash, *, completed_anchor_day=None):
    """Pure state transformation; caller must CAS before exposing session."""
    anchor = completed_anchor_day or day
    a.require(a.dt.date.fromisoformat(anchor).isoformat() == anchor and anchor <= day and
              ledger['days'].get(anchor, {}).get('status') == 'complete', 'Original anchor day incomplete')
    a.require(not any(r.get('status') != 'complete' for r in ledger.get('additional_batches', {}).values()),
              'Unresolved additional production exists')
    sessions = ledger.setdefault('additional_sessions', {})
    a.require(run not in sessions and all(s.get('status') == 'complete' for s in sessions.values()),
              'Unresolved or reused additional transport session')
    sessions[run] = {'status': 'waiting', 'identity': identity, 'production_date': day,
                     'batch_id': batch, 'approval_hash': approval_hash, 'completed_anchor_day': anchor}


def assert_session(ledger, run, identity, day, batch, approval_hash, *, completed_anchor_day=None):
    anchor = completed_anchor_day or day
    expected = {'status': 'waiting', 'identity': identity, 'production_date': day,
                'batch_id': batch, 'approval_hash': approval_hash, 'completed_anchor_day': anchor}
    a.require(ledger.get('additional_sessions', {}).get(run) == expected and
              c.p.now().date().isoformat() == day, 'Additional transport session changed')


def execute():
    stage = 'context'; receiver = None; root = None; values = None; identity = None
    try:
        run, attempt, signing_key, values = dispatch_context()
        day = values['ADDITIONAL_PRODUCTION_DATE']; batch = values['ADDITIONAL_BATCH_ID']
        approval_hash = values['ADDITIONAL_APPROVAL_HASH']
        runtime = ar.Runtime(values['ADDITIONAL_APPROVED_HEAD'])
        runtime.assert_context()
        receiver = t.create_receiver(ar.REPOSITORY, run, attempt, day, signing_key, ttl=600)
        identity = staged.session_record(receiver.announcement)
        root = c.p.ROOT / 'runs' / day / 'additional' / batch
        stage = 'session_reservation'
        ledger, sha = c.journal_get()
        register_session(ledger, run, identity, day, batch, approval_hash, completed_anchor_day=values['ADDITIONAL_ANCHOR_DAY'])
        c.journal_put(ledger, sha)
        path = 'state/staging/additional-' + run + '-1.json'
        print('Additional public session: ' + json.dumps(receiver.announcement, sort_keys=True), flush=True)
        print('Additional ciphertext path: ' + path, flush=True)
        stage = 'await_envelope'
        while time.monotonic() <= receiver.deadline:
            incoming = staged.fetch_envelope(path)
            if incoming is None:
                time.sleep(15)
                continue
            stage = 'decrypt'
            payload = at.unwrap_payload(receiver.receive(incoming[0]))
            packet = payload['packet']; approval = payload['approval']
            a.require(packet.get('production_date') == day and packet.get('batch_id') == batch and
                      packet.get('completed_anchor_day', day) == values['ADDITIONAL_ANCHOR_DAY'],
                      'Additional input identity mismatch')
            a.require(a.digest(approval) == approval_hash, 'Additional approval hash mismatch')
            stage = 'input_validation'
            ledger, sha = c.journal_get()
            assert_session(ledger, run, identity, day, batch, approval_hash, completed_anchor_day=values['ADDITIONAL_ANCHOR_DAY'])
            a.validate(packet, approval, approval_hash, ledger, runtime.config(), day)
            runtime.verified_tts_terms = payload['verified_tts_terms']
            runtime.bind_authorization(approval, packet)
            runtime.validate_job(packet['job'])
            # Only authenticated/approved inputs may trigger these read-only asset fetches.
            stage = 'materialize_assets'
            runtime.materialize_media(packet)
            runtime.validate_media(packet)
            c.p.write(root / 'approved-input.json', payload)
            stage = 'production'
            result = a.run(packet, approval, approval_hash, runtime)
            stage = 'session_completion'
            ledger, sha = c.journal_get()
            # Completion receipts may be written after midnight for accepted uploads.
            expected = ledger.get('additional_sessions', {}).get(run, {})
            a.require(expected.get('identity') == identity and expected.get('status') == 'waiting',
                      'Additional completion session mismatch')
            expected['status'] = 'complete'
            c.journal_put(ledger, sha)
            print('Published additional Short: ' + result['url'], flush=True)
            return result
        raise ValueError('Additional encrypted input expired')
    except Exception:
        # Never echo parser/provider strings, scripts, credentials or account terms.
        print('Additional production stopped at stage=' + stage + '; reconcile saved state.', flush=True)
        raise RuntimeError('Additional production stopped; no automatic retry') from None
    finally:
        if receiver is not None: receiver.close()
        if root is not None and values is not None and identity is not None:
            try:
                # Collect output from the legacy renderer's unique date/channel/job path.
                # Approved input/checkpoints are already under root. Copy only exact known
                # result files, never a state directory or credential path.
                input_path = root / 'approved-input.json'
                if input_path.is_file():
                    import shutil
                    saved = c.p.read(input_path); job = saved['packet']['job']
                    folder = c.p.ROOT / 'runs' / values['ADDITIONAL_PRODUCTION_DATE'] / job['channel'] / job['id']
                    for name in ('short.mp4', 'narration.mp3', 'narration.json', 'captions.srt',
                                 'upload-metadata.json', 'upload-receipt.json', 'manifest.json'):
                        source = folder / name
                        if source.is_file() and not source.is_symlink():
                            target = root / 'produced' / name; target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, target)
                at.encrypt_recovery(root, c.p.ROOT / 'private-artifacts' / 'additional-recovery.enc.json',
                                    values['ADDITIONAL_RECOVERY_PUBLIC_KEY'],
                                    {'repository': ar.REPOSITORY, 'run_id': identity['run_id'],
                                     'run_attempt': identity['run_attempt'], 'day': values['ADDITIONAL_PRODUCTION_DATE'],
                                     'batch_id': values['ADDITIONAL_BATCH_ID'],
                                     'approval_hash': values['ADDITIONAL_APPROVAL_HASH']})
            except Exception:
                print('Encrypted recovery artifact unavailable; preserve hold and reconcile.', flush=True)


if __name__ == '__main__':
    try:
        execute()
    except Exception:
        raise SystemExit(1)
