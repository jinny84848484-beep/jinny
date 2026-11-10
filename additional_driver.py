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
               'ADDITIONAL_APPROVED_HEAD', 'ADDITIONAL_RECOVERY_PUBLIC_KEY', 'ADDITIONAL_CONTINUATION_RECORD_HASH')}
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
    if values['ADDITIONAL_CONTINUATION_RECORD_HASH']:
        a.require(re.fullmatch('[0-9a-f]{64}', values['ADDITIONAL_CONTINUATION_RECORD_HASH']),
                  'Invalid continuation dispatch hash')
    at.validate_recovery_key(values['ADDITIONAL_RECOVERY_PUBLIC_KEY'])
    return run, attempt, signing_key, values


def verified_no_effects_session(record):
    """Validate a preserved, independently reconciled pre-production terminal record."""
    if not isinstance(record, dict) or record.get('status') != 'resolved_no_effects':
        return False
    prior = copy.deepcopy(record)
    resolution = prior.pop('resolution', None)
    prior['status'] = 'waiting'
    if not isinstance(resolution, dict) or set(resolution) != {'previous_session_hash', 'proof', 'proof_hash'}:
        return False
    proof = resolution['proof']
    if not isinstance(proof, dict) or set(proof) != {'run_id','run_attempt','head_sha','conclusion','stage',
            'session_hash','recovery_zip_sha256','recovery_entries','ledger_sha','checked_at'}:
        return False
    try:
        checked = a.dt.datetime.fromisoformat(proof['checked_at'].replace('Z', '+00:00'))
        if checked.tzinfo is None or checked.utcoffset() is None:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(proof['run_id'], str) or re.fullmatch('[0-9]+', proof['run_id']) is None:
        return False
    if not isinstance(prior.get('identity'), dict) or not {'run_id','run_attempt'} <= set(prior['identity']):
        return False
    return (resolution['previous_session_hash'] == a.digest(prior) == proof['session_hash']
            and resolution['proof_hash'] == a.digest(proof)
            and proof['run_id'] == prior['identity']['run_id']
            and proof['run_attempt'] == prior['identity']['run_attempt'] == '1'
            and proof['conclusion'] == 'failure' and proof['stage'] == 'materialize_assets'
            and proof['recovery_entries'] == []
            and all(isinstance(proof[k], str) and re.fullmatch('[0-9a-f]{40}', proof[k])
                    for k in ('head_sha','ledger_sha'))
            and isinstance(proof['recovery_zip_sha256'], str)
            and re.fullmatch('[0-9a-f]{64}', proof['recovery_zip_sha256']) is not None
            and isinstance(proof['checked_at'], str) and bool(proof['checked_at']))


def reconcile_preproduction_session(ledger, run, proof, *, expected_session_hash, expected_proof_hash):
    """Pure transformation after operator independently verifies GitHub/run/recovery evidence.

    Does not fetch evidence, clear production holds, or certify anything itself.
    Caller must CAS against proof.ledger_sha and inspect exact deployed code/head.
    """
    record = ledger.get('additional_sessions', {}).get(run)
    a.require(isinstance(record, dict) and record.get('status') == 'waiting'
              and a.digest(record) == expected_session_hash
              and a.digest(proof) == expected_proof_hash, 'Reconciliation evidence mismatch')
    a.require(not ledger.get('additional_batches'), 'Production records prohibit session-only reconciliation')
    candidate = copy.deepcopy(record)
    candidate['status'] = 'resolved_no_effects'
    candidate['resolution'] = {'previous_session_hash': expected_session_hash,
                               'proof': copy.deepcopy(proof), 'proof_hash': expected_proof_hash}
    a.require(verified_no_effects_session(candidate), 'Unverified preproduction terminal evidence')
    a.require(proof['run_id'] == run, 'Reconciliation run mismatch')
    ledger['additional_sessions'][run] = candidate
    return candidate


def verified_completed_review_continuation(ledger, source_run, source_session):
    """Recognize completed replacement without rewriting the original waiting audit."""
    record = ledger.get('additional_batches', {}).get(source_session.get('batch_id'), {})
    history = record.get('review_continuation', {})
    prior = history.get('previous_record', {})
    auth = history.get('authorization', {})
    current_session = ledger.get('additional_sessions', {}).get(record.get('run_id'), {})
    receipts = [p for p in ledger.get('publications', [])
                if p.get('additional_batch_id') == source_session.get('batch_id')]
    return (source_session.get('status') == 'waiting' and record.get('status') == 'complete'
            and prior.get('status') == 'review_completed' and prior.get('run_id') == source_run
            and auth.get('previous_record_hash') == a.digest(prior)
            and auth.get('previous_review_response_hash') == history.get('previous_review_response_hash')
            == prior.get('checkpoints', {}).get('review', {}).get('response_hash')
            and prior.get('approval_hash') == source_session.get('approval_hash')
            and prior.get('production_date') == source_session.get('production_date')
            and record.get('run_id') != source_run and current_session.get('status') == 'complete'
            and current_session.get('approval_hash') == record.get('approval_hash')
            and current_session.get('batch_id') == source_session.get('batch_id')
            and len(receipts) == 1 and bool(record.get('video_id'))
            and receipts[0].get('video_id') == record.get('video_id')
            and receipts[0].get('job_id') == record.get('job_id'))


def register_session(ledger, run, identity, day, batch, approval_hash, *, completed_anchor_day=None,
                     continuation_record_hash=None):
    """Pure state transformation; caller must CAS before exposing session."""
    anchor = completed_anchor_day or day
    a.require(a.dt.date.fromisoformat(anchor).isoformat() == anchor and anchor <= day and
              ledger['days'].get(anchor, {}).get('status') == 'complete', 'Original anchor day incomplete')
    source_run = None
    if continuation_record_hash:
        prior = ledger.get('additional_batches', {}).get(batch, {})
        a.require(a.digest(prior) == continuation_record_hash and
                  prior.get('status') == 'review_completed' and 'review_continuation' not in prior and
                  prior.get('production_date') == day and prior.get('completed_anchor_day') == anchor,
                  'Continuation dispatch record mismatch')
        source_run = prior.get('run_id')
        previous_session = ledger.get('additional_sessions', {}).get(source_run, {})
        a.require(source_run != run and previous_session.get('status') == 'waiting' and
                  previous_session.get('batch_id') == batch and
                  previous_session.get('production_date') == day and
                  previous_session.get('approval_hash') == prior.get('approval_hash'),
                  'Continuation source session mismatch')
    from additional_expiry import batch_resolved, verified_expired_session
    a.require(not any(not batch_resolved(r) for key, r in
                      ledger.get('additional_batches', {}).items()
                      if not (continuation_record_hash and key == batch)),
              'Unresolved additional production exists')
    sessions = ledger.setdefault('additional_sessions', {})
    a.require(run not in sessions and all(s.get('status') == 'complete' or verified_no_effects_session(s)
              or verified_expired_session(ledger, s)
              or verified_completed_review_continuation(ledger, key, s) or key == source_run for key, s in sessions.items()),
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
        receiver = t.create_receiver(ar.REPOSITORY, run, attempt, day, signing_key, ttl=600, max_bytes=524288)
        identity = staged.session_record(receiver.announcement)
        root = c.p.ROOT / 'runs' / day / 'additional' / batch
        stage = 'session_reservation'
        ledger, sha = c.journal_get()
        register_session(ledger, run, identity, day, batch, approval_hash,
                         completed_anchor_day=values['ADDITIONAL_ANCHOR_DAY'],
                         continuation_record_hash=values['ADDITIONAL_CONTINUATION_RECORD_HASH'])
        c.journal_put(ledger, sha)
        path = 'state/staging/additional-' + run + '-1.json'
        print('Additional public session: ' + json.dumps(receiver.announcement, sort_keys=True), flush=True)
        print('Additional ciphertext path: ' + path, flush=True)
        stage = 'await_envelope'
        while time.monotonic() <= receiver.deadline:
            incoming = staged.fetch_envelope(path, max_bytes=524288)
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
            evidence = payload.get('continuation_evidence')
            if values['ADDITIONAL_CONTINUATION_RECORD_HASH']:
                import additional_review_continuation as continuation
                a.require(approval.get('review_continuation', {}).get('previous_record_hash') ==
                          values['ADDITIONAL_CONTINUATION_RECORD_HASH'], 'Continuation dispatch authorization mismatch')
                continuation.validate(packet, approval, approval_hash, ledger, runtime.config(), day, evidence)
            else:
                a.require(evidence is None and 'review_continuation' not in approval,
                          'Continuation requires explicit dispatch')
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
            result = a.run(packet, approval, approval_hash, runtime, continuation_evidence=evidence)
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
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError and str(exc) in {"graphics_bytes_invalid", "graphics_preview_failed"} else "unclassified"
        print("Additional fixed failure code=" + code, flush=True)
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
