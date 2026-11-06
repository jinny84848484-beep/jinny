"""Explicit, one-shot paid FAIL continuation. No network calls or state writes here.

Evidence is supplied inside authenticated transport and bound by a separately
trusted approval hash. The original reservation and failed checkpoint are never
released; validation removes them only from a disposable validation projection.
"""
import copy
import json
import additional_batch as a


def validate(packet, approval, trusted_hash, ledger, config, today, evidence):
    a.require(a.digest(approval) == trusted_hash, 'Continuation approval hash mismatch')
    a.require(isinstance(evidence, dict) and set(evidence) ==
              {'previous_approval', 'previous_review_response'},
              'Exact continuation evidence required')
    batch = packet['batch_id']
    prior = ledger.get('additional_batches', {}).get(batch)
    auth = approval.get('review_continuation', {})
    a.require(isinstance(prior, dict) and isinstance(auth, dict) and set(auth) == {
        'previous_record_hash', 'previous_packet_hash', 'previous_approval_hash',
        'previous_review_response_hash', 'previous_run_id', 'total_review_requests',
        'used_review_requests', 'remaining_review_requests', 'remaining_render_qa_requests',
        'remaining_tts_requests'}, 'Exact continuation authorization required')
    old_approval = evidence['previous_approval']
    response = evidence['previous_review_response']
    a.require(a.digest(prior) == auth['previous_record_hash'] and
              auth['previous_packet_hash'] == prior.get('packet_hash') and
              a.digest(old_approval) == auth['previous_approval_hash'] == prior.get('approval_hash') and
              old_approval.get('packet_hash') == prior.get('packet_hash') and
              old_approval.get('publication', {}).get('job_hash') == a.digest(packet['job']) and
              prior.get('run_id') == auth['previous_run_id'], 'Continuation provenance mismatch')
    a.require(set(prior) == {'production_date', 'completed_anchor_day', 'job_id', 'packet_hash',
              'approval_hash', 'event_keys', 'tts_reservation', 'status', 'checkpoints', 'run_id', 'created_at'}
              and prior.get('status') == 'review_completed' and
              set(prior.get('checkpoints', {})) == {'review'},
              'Only a first completed review before TTS may continue')
    checkpoint = prior['checkpoints']['review']
    a.require(set(checkpoint) == {'status', 'request_hash', 'attempts', 'response_hash'} and
              checkpoint['status'] == 'completed' and type(checkpoint['attempts']) is int and
              checkpoint['attempts'] == 1 and checkpoint['response_hash'] ==
              auth['previous_review_response_hash'] == a.digest(response),
              'Original review checkpoint mismatch')
    a.require(response.get('status') == 'completed', 'Original review response incomplete')
    text = '\n'.join(part.get('text', '') for item in response.get('output', [])
                     for part in item.get('content', []) if part.get('type') == 'output_text')
    decision = json.loads(text)
    a.require(isinstance(decision, dict) and decision.get('status') == 'FAIL' and
              isinstance(decision.get('issues'), list) and decision['issues'] and
              decision.get('selection') == {}, 'Only an explicit paid FAIL may continue')
    a.require(packet['production_date'] == prior['production_date'] and
              packet.get('completed_anchor_day', packet['production_date']) == prior['completed_anchor_day'] and
              packet['job']['id'] == prior['job_id'] and
              packet.get('event_keys') == prior['event_keys'] and
              packet.get('media_policy') == 'original_graphics',
              'Continuation cannot change reserved identity or media policy')
    expected = {'total_review_requests': 3, 'used_review_requests': 1,
                'remaining_review_requests': 1, 'remaining_render_qa_requests': 1,
                'remaining_tts_requests': 1}
    a.require(all(type(auth[k]) is int and auth[k] == v for k, v in expected.items()),
              'Continuation request allowance exceeded')
    chars = a.billable_characters(packet['job'], config)
    a.require(chars <= prior['tts_reservation'] and
              approval['cost_scope'].get('tts_characters') == prior['tts_reservation'],
              'Continuation must retain original TTS reservation')
    a.require(not any(p.get('additional_batch_id') == batch or p.get('job_id') == prior['job_id']
                      for p in ledger['publications']), 'Continuation already published')
    # All original validation (including historical audit, costs, duplicates and
    # daily cap) is applied. Only this exact consumed reservation is substituted.
    projection = copy.deepcopy(ledger)
    del projection['additional_batches'][batch]
    plan = a.validate(packet, approval, trusted_hash, projection, config, today)
    # Reserve the original (possibly larger) amount, never release unused credits.
    a.require(plan['prior_characters'] + prior['tts_reservation'] <= config['daily_character_cap'],
              'Aggregate daily TTS cap exceeded')
    plan['day_total_reserved'] = plan['prior_characters'] + prior['tts_reservation']
    return plan, copy.deepcopy(prior)
