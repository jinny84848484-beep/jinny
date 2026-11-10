"""Terminal expiry for a stale additional batch that stopped after review, before any TTS.

Why: a batch left at 'review_completed' can only be continued on its own production_date
(register_session/assert_session require today == day). After that date nothing can move it,
and every guard that demands status == 'complete' blocks all scheduled production forever.

This module never deletes audit data. It keeps the exact previous record and its hash,
and only accepts the narrow, provably side-effect-free shape:
  - production_date strictly before today (KST)
  - status 'review_completed', checkpoints == {'review'} (no TTS / render / upload checkpoint)
  - no publication with that batch id or job id
  - operator-supplied hash of the record matches (CAS on the exact record they inspected)
"""
from __future__ import annotations
import copy
import additional_batch as a

EXPIRED = 'expired_no_effects'
EXTERNAL = 'resolved_external_upload'
SESSION_EXPIRED = 'resolved_expired'


def batch_resolved(record):
    """True for a terminal additional batch: completed, or a verified expiry."""
    if not isinstance(record, dict):
        return False
    if record.get('status') == 'complete':
        return True
    return verified_expired_batch(record) or verified_external_batch(record)


def verified_external_batch(record):
    """Stopped after review, then uploaded outside this pipeline by an operator."""
    if record.get('status') != EXTERNAL:
        return False
    exp = record.get('expiry')
    if not isinstance(exp, dict) or set(exp) != {'previous_record', 'previous_record_hash', 'expired_on', 'reason', 'video_id'}:
        return False
    prior = exp['previous_record']
    return (isinstance(prior, dict) and a.digest(prior) == exp['previous_record_hash']
            and prior.get('status') == 'review_completed'
            and set(prior.get('checkpoints', {})) == {'review'}
            and prior.get('production_date', '9999') < exp['expired_on']
            and exp['reason'] == 'uploaded_outside_pipeline'
            and isinstance(exp['video_id'], str) and len(exp['video_id']) == 11)


def verified_expired_batch(record):
    if record.get('status') != EXPIRED:
        return False
    exp = record.get('expiry')
    if not isinstance(exp, dict) or set(exp) != {'previous_record', 'previous_record_hash', 'expired_on', 'reason'}:
        return False
    prior = exp['previous_record']
    return (isinstance(prior, dict) and a.digest(prior) == exp['previous_record_hash']
            and prior.get('status') == 'review_completed'
            and set(prior.get('checkpoints', {})) == {'review'}
            and prior.get('production_date', '9999') < exp['expired_on']
            and exp['reason'] == 'stale_review_completed_before_tts')


def verified_expired_session(ledger, record):
    if not isinstance(record, dict) or record.get('status') != SESSION_EXPIRED:
        return False
    batch = ledger.get('additional_batches', {}).get(record.get('batch_id'), {})
    return verified_expired_batch(batch) or verified_external_batch(batch)


def expire_stale_batch(ledger, batch_id, *, today, expected_record_hash):
    """Pure transformation. Caller CASes the journal. Returns the new ledger copy."""
    out = copy.deepcopy(ledger)
    batches = out.get('additional_batches', {})
    a.require(batch_id in batches, 'Unknown additional batch')
    prior = batches[batch_id]
    a.require(a.digest(prior) == expected_record_hash, 'Record changed since operator inspection')
    a.require(prior.get('status') == 'review_completed', 'Only a review_completed batch can expire')
    a.require(set(prior.get('checkpoints', {})) == {'review'}, 'Batch has post-review side effects')
    a.require(prior.get('production_date', '') < today, 'Batch is not stale yet')
    a.require(not any(p.get('additional_batch_id') == batch_id or p.get('job_id') == prior.get('job_id')
                      for p in out.get('publications', [])), 'Batch has a publication')
    batches[batch_id] = {
        'status': EXPIRED,
        'production_date': prior.get('production_date'),
        'job_id': prior.get('job_id'),
        'event_keys': prior.get('event_keys', []),   # keep topics reserved against duplicates
        'expiry': {'previous_record': copy.deepcopy(prior), 'previous_record_hash': expected_record_hash,
                   'expired_on': today, 'reason': 'stale_review_completed_before_tts'},
    }
    for run, s in out.get('additional_sessions', {}).items():
        if s.get('batch_id') == batch_id and s.get('status') == 'waiting':
            s['status'] = SESSION_EXPIRED
            s['expired_on'] = today
    return out


def resolve_external_upload(ledger, batch_id, *, today, expected_record_hash, video_id, channel, channel_id, title=''):
    """Pure transformation for a batch whose video was published manually, outside the pipeline.

    Records the operator-verified video as a publication so duplicate checks and metrics see it.
    """
    import re
    a.require(isinstance(video_id, str) and re.fullmatch('[A-Za-z0-9_-]{11}', video_id) is not None, 'Invalid video id')
    a.require(not any(p.get('video_id') == video_id for p in ledger.get('publications', [])), 'Video already recorded')
    out = expire_stale_batch(ledger, batch_id, today=today, expected_record_hash=expected_record_hash)
    rec = out['additional_batches'][batch_id]
    rec['status'] = EXTERNAL
    rec['video_id'] = video_id
    rec['expiry']['reason'] = 'uploaded_outside_pipeline'
    rec['expiry']['video_id'] = video_id
    keys = rec.get('event_keys') or [rec.get('job_id')]
    out['publications'].append({'job_id': rec.get('job_id'), 'topic_key': keys[0], 'category': None,
                                'channel': channel, 'channel_id': channel_id, 'title': title,
                                'video_id': video_id, 'url': 'https://www.youtube.com/shorts/' + video_id,
                                'confirmed_at': today, 'experiment': {},
                                'publication_method': 'external_manual', 'additional_batch_id': batch_id})
    return out
