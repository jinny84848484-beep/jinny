import copy
import json
import unittest
from unittest.mock import patch
import additional_batch as a
import additional_review_continuation as rc
import additional_driver as d
import additional_transport as t
from test_additional_batch import fixtures, FakeRuntime, DAY


class PaidReviewContinuationTests(unittest.TestCase):
    def setUp(self):
        self.p, self.ap, self.l, self.c = fixtures()
        self.p['media_policy'] = 'original_graphics'
        self.ap['packet_hash'] = a.digest(self.p)
        self.ap['cost_scope'].update(render_qa_requests=1, render_qa_model=a.MODEL,
            render_qa_max_output_tokens=1200, render_qa_web_calls=0, tts_characters=250)
        response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text',
            'text': json.dumps({'status': 'FAIL', 'issues': [{'code': 'SOURCE'}], 'selection': {}})}]}]}
        self.old = {'production_date': DAY, 'completed_anchor_day': DAY,
            'job_id': self.p['job']['id'], 'packet_hash': a.digest(self.p),
            'approval_hash': a.digest(self.ap), 'event_keys': self.p['event_keys'],
            'tts_reservation': 250, 'status': 'review_completed',
            'checkpoints': {'review': {'status': 'completed', 'attempts': 1,
                'request_hash': 'a'*64, 'response_hash': a.digest(response)}},
            'run_id': 'source-run', 'created_at': DAY + 'T09:00:00+09:00'}
        self.l['additional_batches'] = {self.p['batch_id']: copy.deepcopy(self.old)}
        self.l['additional_sessions'] = {'source-run': {'status': 'waiting',
            'batch_id': self.p['batch_id'], 'production_date': DAY,
            'approval_hash': self.old['approval_hash']}}
        self.evidence = {'previous_approval': copy.deepcopy(self.ap), 'previous_review_response': response}
        self.p['review_notes'] = 'Address previous failed graphic review without changing the script.'
        self.ap['packet_hash'] = a.digest(self.p)
        self.ap['review_continuation'] = {
            'previous_record_hash': a.digest(self.old), 'previous_packet_hash': self.old['packet_hash'],
            'previous_approval_hash': self.old['approval_hash'],
            'previous_review_response_hash': a.digest(response), 'previous_run_id': 'source-run',
            'total_review_requests': 3, 'used_review_requests': 1, 'remaining_review_requests': 1,
            'remaining_render_qa_requests': 1, 'remaining_tts_requests': 1}

    def validate(self):
        return rc.validate(self.p, self.ap, a.digest(self.ap), self.l, self.c, DAY, self.evidence)

    def run_fake(self, r):
        return a.run(self.p, self.ap, a.digest(self.ap), r, continuation_evidence=self.evidence)

    def test_success_preserves_failed_audit_and_single_reservation(self):
        before = copy.deepcopy(self.l)
        plan, original = self.validate()
        self.assertEqual(self.l, before)
        self.assertEqual(plan['day_total_reserved'], 1450)
        self.assertEqual(original, self.old)
        r = FakeRuntime(self.l, self.c); self.run_fake(r)
        record = r.ledger['additional_batches'][self.p['batch_id']]
        self.assertEqual(record['review_continuation']['previous_record'], self.old)
        self.assertEqual(record['review_continuation']['previous_review_response_hash'], a.digest(self.evidence['previous_review_response']))
        self.assertNotIn('previous_review_response', record['review_continuation'])
        self.assertEqual(record['tts_reservation'], 250)
        self.assertEqual(r.ledger['days'], self.l['days'])
        self.assertEqual(r.ledger['additional_sessions'], self.l['additional_sessions'])
        for effect in ('review', 'tts', 'render_qa', 'upload'):
            self.assertEqual(r.effects.count(effect), 1)
        effects = r.effects[:]
        with self.assertRaises(ValueError): self.run_fake(r)
        self.assertEqual(r.effects, effects)

    def test_failures_stop_and_never_replay(self):
        for fail in ('cas-1', 'checkpoint', 'cas-2', 'review', 'cas-3', 'review_fail',
                     'media', 'cas-4', 'tts', 'cas-5', 'render_qa', 'render_qa_fail',
                     'channel', 'upload'):
            with self.subTest(fail=fail):
                r = FakeRuntime(self.l, self.c); r.fail = fail
                with self.assertRaises((ValueError, RuntimeError)): self.run_fake(r)
                if fail in ('cas-1', 'checkpoint', 'cas-2', 'review', 'cas-3', 'review_fail', 'media', 'cas-4'):
                    self.assertNotIn('tts', r.effects)
                self.assertNotIn('upload', r.effects) if fail != 'upload' else None
                if fail != 'cas-1':
                    before = r.effects[:]; r.fail = None
                    with self.assertRaises(ValueError): self.run_fake(r)
                    self.assertEqual(r.effects, before)

    def test_each_authorization_binding_and_budget_fails_closed(self):
        for key in self.ap['review_continuation']:
            with self.subTest(key=key):
                original = copy.deepcopy(self.ap)
                v = self.ap['review_continuation'][key]
                self.ap['review_continuation'][key] = v + 1 if type(v) is int else 'changed'
                with self.assertRaises(ValueError): self.validate()
                self.ap = original

    def test_fail_response_must_be_exact_completed_fail(self):
        for mutation in ('altered', 'PASS', 'incomplete', 'no_issues'):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.evidence)
                response = self.evidence['previous_review_response']
                if mutation == 'altered': response['other'] = 1
                elif mutation == 'incomplete': response['status'] = 'incomplete'
                else:
                    decision = json.loads(response['output'][0]['content'][0]['text'])
                    if mutation == 'PASS': decision['status'] = 'PASS'
                    else: decision['issues'] = []
                    response['output'][0]['content'][0]['text'] = json.dumps(decision)
                with self.assertRaises(ValueError): self.validate()
                self.evidence = original

    def test_live_record_tts_or_unknown_marker_cannot_be_overridden(self):
        for key, val in [('status', 'tts_submitted'), ('rendered', True), ('retry_hold', False)]:
            original = copy.deepcopy(self.l)
            prior = self.l['additional_batches'][self.p['batch_id']]; prior[key] = val
            self.ap['review_continuation']['previous_record_hash'] = a.digest(prior)
            with self.assertRaises(ValueError): self.validate()
            self.l = original
        self.ap['review_continuation']['previous_record_hash'] = a.digest(self.old)

    def test_changed_job_even_with_new_publication_hash_rejected(self):
        self.p['job']['title'] = 'different'
        self.ap['packet_hash'] = a.digest(self.p)
        self.ap['publication']['job_hash'] = a.digest(self.p['job'])
        with self.assertRaises(ValueError): self.validate()

    def test_day_and_duplicate_and_budget_guards(self):
        with self.assertRaises(ValueError):
            rc.validate(self.p, self.ap, a.digest(self.ap), self.l, self.c, '2026-11-07', self.evidence)
        self.c['daily_character_cap'] = 1400; self.ap['config_hash'] = a.digest(self.c)
        with self.assertRaisesRegex(ValueError, 'cap exceeded'): self.validate()

    def test_missing_evidence_cannot_use_normal_path(self):
        r = FakeRuntime(self.l, self.c)
        with self.assertRaises(ValueError): a.run(self.p, self.ap, a.digest(self.ap), r)
        self.assertFalse(r.effects)

    def test_cas_failure_preserves_original_ledger_and_no_paid_calls(self):
        r = FakeRuntime(self.l, self.c); r.fail = 'cas-1'
        with self.assertRaises(RuntimeError): self.run_fake(r)
        self.assertEqual(r.ledger, self.l); self.assertFalse(r.effects)

    def test_dispatch_preserves_source_session_and_blocks_second_session(self):
        prior = copy.deepcopy(self.l['additional_sessions']['source-run'])
        with self.assertRaises(ValueError):
            d.register_session(self.l, 'new', {}, DAY, self.p['batch_id'], a.digest(self.ap))
        d.register_session(self.l, 'new', {}, DAY, self.p['batch_id'], a.digest(self.ap),
                           continuation_record_hash=a.digest(self.old))
        self.assertEqual(self.l['additional_sessions']['source-run'], prior)
        with self.assertRaises(ValueError):
            d.register_session(self.l, 'another', {}, DAY, self.p['batch_id'], a.digest(self.ap),
                               continuation_record_hash=a.digest(self.old))

    def test_completed_continuation_unblocks_source_without_rewriting_it(self):
        r = FakeRuntime(self.l, self.c); self.run_fake(r)
        r.ledger['additional_sessions'][r.run_id()] = {'status': 'complete',
            'batch_id': self.p['batch_id'], 'approval_hash': a.digest(self.ap)}
        original_session = copy.deepcopy(r.ledger['additional_sessions']['source-run'])
        self.assertTrue(d.verified_completed_review_continuation(r.ledger, 'source-run', original_session))
        d.register_session(r.ledger, 'future', {}, DAY, DAY + '-extra-02', 'f'*64)
        self.assertEqual(r.ledger['additional_sessions']['source-run'], original_session)
        r.ledger['publications'].pop()
        self.assertFalse(d.verified_completed_review_continuation(r.ledger, 'source-run', original_session))

    def test_transport_evidence_round_trip(self):
        payload = t.unwrap_payload(t.wrap_payload(self.p, self.ap, continuation_evidence=self.evidence))
        self.assertEqual(payload['continuation_evidence'], self.evidence)


if __name__ == '__main__': unittest.main()
