"""Manual additional single-story path. No external effects on import.

Production invocation requires a separately trusted approval hash. An input file
cannot approve itself. The existing daily runner and recovery paths are unchanged.
No model-generated draft, research retry, automatic resume or hold clearing.
"""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

MODEL = 'gpt-5.4'
REVIEW_OUTPUT_TOKENS = 2600
REVIEW_TOOL_CALLS = 3
CHANNEL = 'ojirap'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def narration(job):
    return ' '.join(s['narration'].strip() for s in job['scenes'])


def billable_characters(job, config):
    delivery = config['channels'][job['channel']].get('delivery', '')
    return len(('[' + delivery + '] ' if delivery else '') + narration(job))


def date_of(value):
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.tzinfo is not None, 'Timestamp needs timezone')
    return stamp.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()


def publication_may_need_day_accounting(publication, day):
    if publication.get('at'):
        return date_of(publication['at']) == day
    # Bootstrap receipts record confirmation, not exact upload time. Confirmation
    # before the actual day proves it was already published earlier. Otherwise
    # conservatively require accounting; never invent a publication timestamp.
    if publication.get('confirmed_at'):
        return date_of(publication['confirmed_at']) >= day
    return True


def historical_identity(ledger):
    # The audit binds all legacy history; new batches have their own reservations.
    return digest({'days': ledger['days'], 'publications': [
        p for p in ledger['publications'] if not p.get('additional_batch_id')]})


def validate(packet, approval, trusted_approval_hash, ledger, config, today):
    require(re.fullmatch('[0-9a-f]{64}', trusted_approval_hash or '') is not None,
            'Missing independently trusted approval hash')
    require(digest(approval) == trusted_approval_hash, 'Approval hash mismatch')
    require(approval.get('packet_hash') == digest(packet), 'Approved content changed')
    day = packet['production_date']; batch = packet['batch_id']; job = packet['job']
    require(dt.date.fromisoformat(day).isoformat() == day and day == today,
            'Production date expired or invalid')
    require(re.fullmatch(re.escape(day) + r'-extra-[0-9]{2}', batch) is not None,
            'Invalid additional batch identity')
    require(job.get('channel') == CHANNEL and job.get('channel_id') ==
            config['channels'][CHANNEL]['channel_id'], 'Only the approved Korean channel is supported')
    require(job.get('id', '').startswith(batch + '-') and
            re.fullmatch('[a-z0-9][a-z0-9-]{2,90}', job['id']) is not None,
            'Invalid batch job identity')
    anchor = packet.get('completed_anchor_day', day)
    require(dt.date.fromisoformat(anchor).isoformat() == anchor and anchor <= day,
            'Completed anchor date invalid or in future')
    require(ledger['days'].get(anchor, {}).get('status') == 'complete',
            'Original daily batch is not complete')
    if anchor != day:
        require(approval.get('completed_anchor') == {'day': anchor, 'record_hash': digest(ledger['days'][anchor])},
                'Explicit immutable completed anchor approval required')
    original_jobs = ledger['days'][anchor].get('jobs', [])
    require(len(original_jobs) == 4 and sorted(j.get('channel') for j in original_jobs) ==
            ['korean', 'korean', 'ojirap', 'ojirap'] and len({j['id'] for j in original_jobs}) == 4,
            'Original four-job daily batch not established')
    for original_job in original_jobs:
        matches = [p for p in ledger['publications'] if p.get('job_id') == original_job['id']]
        require(len(matches) == 1 and bool(matches[0].get('video_id')) and
                matches[0].get('channel') == original_job['channel'] and
                matches[0].get('channel_id') == original_job['channel_id'],
                'Original daily publication receipt missing or inconsistent')
    require(approval.get('publication') == {'channel_id': job['channel_id'],
            'count': 1, 'privacy': 'public', 'job_hash': digest(job)},
            'Exact public content approval missing')
    costs = approval.get('cost_scope', {})
    policy = packet.get('media_policy', 'licensed_photos')
    require(policy in {'licensed_photos', 'original_graphics'}, 'Unsupported media policy')
    if policy == 'original_graphics':
        require(costs.get('render_qa_requests') == 1 and costs.get('render_qa_model') == MODEL
                and costs.get('render_qa_max_output_tokens') == 1200
                and costs.get('render_qa_web_calls') == 0,
                'Actual rendered-frame QA cost scope required for graphics')
    require(costs.get('review_model') == MODEL and costs.get('review_requests') == 1
            and costs.get('max_output_tokens') == REVIEW_OUTPUT_TOKENS
            and costs.get('max_tool_calls') == REVIEW_TOOL_CALLS
            and costs.get('variable_review_charges_accepted') is True,
            'Explicit variable review cost authorization missing')
    chars = billable_characters(job, config)
    require(type(costs.get('tts_characters')) is int and costs['tts_characters'] >= chars
            and costs.get('automatic_topup') is False and costs.get('credit_purchase') is False,
            'TTS allowance and no-purchase instruction required')
    payment = costs.get('tts_payment', {})
    require(payment.get('mode') in {'included', 'metered'}, 'Explicit TTS billing mode required')
    if payment['mode'] == 'metered':
        try:
            ceiling = Decimal(payment['max_total'])
            unit = Decimal(payment['max_cost_per_character'])
        except (KeyError, InvalidOperation, TypeError):
            raise ValueError('Verified metered TTS price bound required') from None
        require(ceiling.is_finite() and unit.is_finite() and ceiling > 0 and unit > 0
                and unit * chars <= ceiling and re.fullmatch('[A-Z]{3}', payment.get('currency', ''))
                and re.fullmatch('[0-9a-f]{64}', payment.get('pricing_evidence_hash', '')),
                'Metered TTS allowance insufficient or unverified')
    require(approval.get('config_hash') == digest(config), 'Voice/config authorization mismatch')
    require(200 <= len(narration(job)) <= 450, 'Korean narration outside bounds')
    aliases = packet.get('event_keys')
    require(isinstance(aliases, list) and aliases and all(isinstance(k, str) and
            re.fullmatch('[a-z0-9][a-z0-9-]{2,150}', k) for k in aliases)
            and job.get('topic_key') in aliases and len(aliases) == len(set(aliases)),
            'Explicit canonical event identities required')
    prior = ledger.get('additional_batches', {}).get(batch)
    if prior:
        require(prior.get('packet_hash') == digest(packet) and
                prior.get('approval_hash') == trusted_approval_hash, 'Existing batch identity changed')
        if prior.get('status') == 'complete':
            matches = [p for p in ledger['publications'] if p.get('additional_batch_id') == batch]
            require(len(matches) == 1 and matches[0].get('job_id') == job['id']
                    and matches[0].get('video_id') == prior.get('video_id'),
                    'Completed batch receipt mismatch')
            return {'complete': True, 'publication': matches[0]}
        raise ValueError('Additional batch already reserved; reconcile without replay')
    require(all(r.get('status') == 'complete' for r in ledger.get('additional_batches', {}).values()),
            'Another additional batch is unresolved; reconcile before starting new work')
    records = list(ledger['days'].values()) + list(ledger.get('additional_batches', {}).values())
    ids = {p.get('job_id') for p in ledger['publications']}
    topics = {p.get('topic_key', '').lower() for p in ledger['publications'][-40:]}
    for pub in ledger['publications']:
        if publication_may_need_day_accounting(pub, day):
            topics.add(pub.get('topic_key', '').lower())
    for record in records:
        ids.update(j['id'] for j in record.get('jobs', []))
        ids.add(record.get('job_id'))
        topics.update(record.get('event_keys', []))
        if record.get('status') != 'complete':
            topics.update(j.get('topic_key', '').lower() for j in record.get('jobs', []))
    require(job['id'] not in ids and not set(aliases).intersection(topics), 'Duplicate job or event')
    audit = approval.get('prior_tts_audit', {})
    if anchor != day:
        require(audit.get('scope') == 'repository_pipeline', 'Actual-day repository usage audit scope required')
    require(audit.get('production_date') == day and
            audit.get('historical_identity') == historical_identity(ledger) and
            audit.get('includes_all_attempts_and_uncertain_submissions') is True,
            'Complete independently reconciled prior TTS audit required')
    entries = audit.get('entries', [])
    require(isinstance(entries, list) and all(isinstance(e, dict) and
            isinstance(e.get('attempt_id'), str) and e['attempt_id'] and
            isinstance(e.get('job_id'), str) and type(e.get('characters')) is int and
            e['characters'] > 0 and e.get('includes_delivery_prefix') is True
            and re.fullmatch('[0-9a-f]{64}', e.get('evidence_hash', ''))
            for e in entries), 'Invalid audited TTS entries')
    require(len({e['attempt_id'] for e in entries}) == len(entries), 'Duplicate audited attempt')
    accounted = {e['job_id'] for e in entries}
    expected = {p['job_id'] for p in ledger['publications']
                if publication_may_need_day_accounting(p, day) and not p.get('additional_batch_id')}
    # Include potentially billed legacy jobs even if upload never completed.
    for record_day, record in ledger['days'].items():
        if record_day == day:
            expected.update(j['id'] for j in record.get('jobs', []))
            if record.get('active_job'):
                expected.add(record['active_job'])
    require(expected <= accounted, 'Prior published or uncertain TTS usage omitted')
    known_jobs = {j['id']: j for r in ledger['days'].values() for j in r.get('jobs', [])}
    for job_id in expected:
        if job_id in known_jobs:
            floor = billable_characters(known_jobs[job_id], config)
            require(all(e['characters'] >= floor for e in entries if e['job_id'] == job_id),
                    'Audited TTS attempt undercounts narration and delivery prefix')
    previous = sum(e['characters'] for e in entries)
    for r in ledger.get('additional_batches', {}).values():
        if r.get('production_date') == day:
            reserve = r.get('tts_reservation')
            require(type(reserve) is int and reserve > 0, 'Unknown additional TTS usage')
            previous += reserve  # Never release on failure, even pre-submission.
    require(previous + chars <= config['daily_character_cap'], 'Aggregate daily TTS cap exceeded')
    return {'complete': False, 'tts_characters': chars, 'prior_characters': previous,
            'day_total_reserved': previous + chars}


def run(packet, approval, trusted_approval_hash, runtime, *, continuation_evidence=None):
    """runtime is an explicit production adapter or an offline test double.

    No retries. All legacy data remains untouched. A failed CAS stops immediately.
    An accepted reservation belongs only to this invocation. Recovery is manual.
    """
    ledger, sha = runtime.journal_get()
    config = runtime.config()
    previous_record = None
    if continuation_evidence is not None:
        import additional_review_continuation as continuation
        plan, previous_record = continuation.validate(packet, approval, trusted_approval_hash,
            ledger, config, runtime.today(), continuation_evidence)
        require(runtime.run_id() != previous_record['run_id'], 'Continuation needs a fresh run')
    else:
        require('review_continuation' not in approval, 'Continuation evidence missing')
        plan = validate(packet, approval, trusted_approval_hash, ledger, config, runtime.today())
    if plan['complete']:
        return plan['publication']
    runtime.bind_authorization(approval, packet)
    runtime.validate_job(packet['job'])
    runtime.validate_media(packet)
    runtime.assert_context()  # Manual Actions, fixed repo/head, original global concurrency.
    runtime.assert_tts_budget(approval['cost_scope'], plan['tts_characters'])
    original_days = copy.deepcopy(ledger['days'])
    original_publications = copy.deepcopy(ledger['publications'])
    batch = packet['batch_id']; day = packet['production_date']; job = copy.deepcopy(packet['job'])
    record = {'production_date': day, 'completed_anchor_day': packet.get('completed_anchor_day', day), 'job_id': job['id'], 'packet_hash': digest(packet),
              'approval_hash': trusted_approval_hash, 'event_keys': packet['event_keys'],
              'tts_reservation': plan['tts_characters'], 'status': 'reserved',
              'checkpoints': {}, 'run_id': runtime.run_id(), 'created_at': runtime.now()}
    if previous_record is not None:
        record['tts_reservation'] = previous_record['tts_reservation']
        record['review_continuation'] = {'previous_record': previous_record,
            'previous_review_response_hash': digest(continuation_evidence['previous_review_response']),
            'authorization': copy.deepcopy(approval['review_continuation'])}
    immutable_continuation = copy.deepcopy(record.get('review_continuation'))
    ledger.setdefault('additional_batches', {})[batch] = record

    def persist(status, check_date=True):
        nonlocal sha
        if check_date:
            require(runtime.today() == day, 'Production date expired')
        require(ledger['days'] == original_days and
                ledger['publications'][:len(original_publications)] == original_publications,
                'Historical state mutation forbidden')
        require(record.get('review_continuation') == immutable_continuation,
                'Original failed review audit mutation forbidden')
        record['status'] = status
        sha = runtime.journal_put(ledger, sha)

    persist('reserved')
    if previous_record is not None:
        runtime.save_checkpoint(batch, 'previous_failed_review',
                                continuation_evidence['previous_review_response'])
    # Actual provider factual/media review, not an input's claimed PASS.
    request = runtime.review_request(packet, MODEL, REVIEW_OUTPUT_TOKENS, REVIEW_TOOL_CALLS)
    record['checkpoints']['review'] = {'status': 'submitted', 'request_hash': digest(request), 'attempts': 1}
    persist('review_submitted')
    response = runtime.review(request)
    runtime.save_checkpoint(batch, 'review', response)
    record['checkpoints']['review'].update(status='completed', response_hash=digest(response))
    persist('review_completed', check_date=False)
    reviewed = runtime.apply_review(packet, response)
    require(reviewed['job']['id'] == job['id'] and narration(reviewed['job']) == narration(job),
            'Review changed approved narration')
    # Review may only attach checked media/verification fields; it cannot rewrite content.
    for key in ('title', 'description', 'topic_key', 'category', 'channel', 'channel_id'):
        require(reviewed['job'].get(key) == job.get(key), 'Review changed approved content')
    runtime.validate_job(reviewed['job'])
    runtime.prepare_media(reviewed)
    runtime.assert_tts_budget(approval['cost_scope'], plan['tts_characters'])
    persist('tts_submitted')
    folder = runtime.render(reviewed)
    persist('rendered', check_date=False)
    if packet.get('media_policy') == 'original_graphics':
        qa_request = runtime.render_qa_request(folder, reviewed)
        record['checkpoints']['render_qa'] = {'status': 'submitted', 'request_hash': digest(qa_request), 'attempts': 1}
        persist('render_qa_submitted')
        qa_result = runtime.render_qa(qa_request)
        runtime.save_checkpoint(batch, 'render_qa', qa_result)
        record['checkpoints']['render_qa'].update(status='completed', response_hash=digest(qa_result))
        persist('render_qa_completed', check_date=False)
        runtime.assert_render_qa(qa_result)
    runtime.verify_target(job['channel'], job['channel_id'])
    persist('upload_submitted')
    receipt = runtime.upload(folder, reviewed['job'])
    require(receipt.get('channel_id') == job['channel_id'] and receipt.get('privacy') == 'public'
            and isinstance(receipt.get('video_id'), str) and receipt['video_id'], 'Upload receipt invalid')
    publication = {'additional_batch_id': batch, 'job_id': job['id'], 'topic_key': job['topic_key'],
                   'category': job['category'], 'channel': job['channel'], 'channel_id': job['channel_id'],
                   'video_id': receipt['video_id'], 'url': 'https://www.youtube.com/shorts/' + receipt['video_id'],
                   'at': runtime.now()}
    ledger['publications'].append(publication)
    record['video_id'] = receipt['video_id']; record['completed_at'] = runtime.now()
    persist('complete', check_date=False)
    return publication
