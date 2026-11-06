import copy
import unittest
import additional_batch as a

DAY = '2026-11-06'


def fixtures():
    config = {'daily_character_cap': 6000, 'channels': {'ojirap': {'channel_id': 'ko-channel', 'delivery': 'calm'}, 'korean': {'channel_id': 'en-channel', 'delivery': 'calm'}}}
    job = {'id': DAY + '-extra-01-stalking-protection', 'channel': 'ojirap', 'channel_id': 'ko-channel',
           'topic_key': 'stalking-protection', 'category': 'society', 'title': '제목', 'description': '설명',
           'scenes': [{'narration': '가' * 60} for _ in range(4)]}
    old_job = copy.deepcopy(job); old_job['id'] = DAY + '-old-story'; old_job['topic_key'] = 'old-story'
    ledger = {'days': {DAY: {'status': 'complete', 'retry_hold': True, 'jobs': [old_job],
                            'checkpoints': {'review': {'status': 'completed', 'sha256': 'e' * 64}}}},
              'publications': [{'job_id': old_job['id'], 'topic_key': 'old-story', 'at': DAY + 'T08:00:00+09:00'}]}
    ledger['days'][DAY]['jobs'] = []
    ledger['publications'] = []
    for index, channel in enumerate(['ojirap', 'ojirap', 'korean', 'korean']):
        previous_job = copy.deepcopy(old_job)
        previous_job.update(id=old_job['id'] + str(index), channel=channel,
                            channel_id=config['channels'][channel]['channel_id'])
        ledger['days'][DAY]['jobs'].append(previous_job)
        ledger['publications'].append({'job_id': previous_job['id'], 'topic_key': 'old-story',
            'channel': channel, 'channel_id': previous_job['channel_id'], 'video_id': 'previous' + str(index),
            'at': DAY + 'T08:00:00+09:00'})
    packet = {'production_date': DAY, 'batch_id': DAY + '-extra-01', 'job': job,
              'event_keys': ['stalking-protection'], 'media': {}}
    approval = {'packet_hash': a.digest(packet), 'config_hash': a.digest(config),
                'publication': {'channel_id': 'ko-channel', 'count': 1, 'privacy': 'public', 'job_hash': a.digest(job)},
                'cost_scope': {'review_model': 'gpt-5.4', 'review_requests': 1, 'max_output_tokens': 2600,
                               'max_tool_calls': 3, 'variable_review_charges_accepted': True,
                               'tts_characters': 500, 'tts_payment': {'mode': 'included'}, 'automatic_topup': False, 'credit_purchase': False},
                'prior_tts_audit': {'production_date': DAY, 'historical_identity': a.historical_identity(ledger),
                                    'includes_all_attempts_and_uncertain_submissions': True,
                                    'entries': [{'attempt_id': 'source-run:tts:1', 'job_id': old_job['id'],
                                                 'characters': 300, 'includes_delivery_prefix': True,
                                                 'evidence_hash': 'a' * 64}]}}
    approval['prior_tts_audit']['entries'] = [
        {'attempt_id': 'source-run:tts:' + str(i), 'job_id': j['id'], 'characters': 300,
         'includes_delivery_prefix': True, 'evidence_hash': 'a' * 64}
        for i, j in enumerate(ledger['days'][DAY]['jobs'])]
    return packet, approval, ledger, config


class FakeRuntime:
    def __init__(self, ledger, config):
        self.ledger = copy.deepcopy(ledger); self.cfg = config; self.sha = 1
        self.effects = []; self.date = DAY; self.fail = None; self.writes = 0
        self.balance = 10000; self.change_date = None

    def config(self): return self.cfg
    def today(self): return self.date
    def now(self): return self.date + 'T22:00:00+09:00'
    def run_id(self): return 'offline-test'
    def journal_get(self): return copy.deepcopy(self.ledger), self.sha
    def journal_put(self, ledger, sha):
        self.writes += 1
        if self.fail == 'cas-' + str(self.writes): raise RuntimeError('CAS failed')
        if sha != self.sha: raise RuntimeError('CAS failed')
        self.ledger = copy.deepcopy(ledger); self.sha += 1
        return self.sha
    def bind_authorization(self, approval, packet): pass
    def validate_job(self, job): pass
    def validate_media(self, packet): pass
    def assert_context(self): pass
    def assert_tts_budget(self, costs, characters):
        if self.balance < characters: raise ValueError('Insufficient included TTS credits')
    def remaining_characters(self): return self.balance
    def review_request(self, packet, model, tokens, tools): return {'packet_hash': a.digest(packet), 'model': model}
    def effect(self, name):
        self.effects.append(name)
        if self.change_date == name: self.date = '2026-11-07'
        if self.fail == name: raise RuntimeError('Uncertain ' + name)
    def review(self, request): self.effect('review'); return {'status': 'completed', 'mock_only': True}
    def save_checkpoint(self, batch, stage, response): self.effect('checkpoint')
    def apply_review(self, packet, response):
        if self.fail == 'review_fail': raise ValueError('Review FAIL')
        return copy.deepcopy(packet)
    def prepare_media(self, packet): self.effect('media')
    def render(self, packet): self.effect('tts'); return 'folder'
    def render_qa_request(self, folder, reviewed): return {'frame_hash': 'test-only'}
    def render_qa(self, request): self.effect('render_qa'); return {'mock_only': True}
    def assert_render_qa(self, response):
        if self.fail == 'render_qa_fail': raise ValueError('Render QA FAIL')
    def verify_target(self, channel, channel_id): self.effect('channel')
    def upload(self, folder, job):
        self.effect('upload')
        return {'channel_id': job['channel_id'], 'privacy': 'public', 'video_id': 'real-receipt-test-double'}


class AdditionalBatchTests(unittest.TestCase):
    def setUp(self): self.p, self.ap, self.l, self.c = fixtures()
    def validate(self): return a.validate(self.p, self.ap, a.digest(self.ap), self.l, self.c, DAY)
    def run_fake(self, runtime): return a.run(self.p, self.ap, a.digest(self.ap), runtime)
    def resign(self):
        self.ap['packet_hash'] = a.digest(self.p)
        self.ap['publication']['job_hash'] = a.digest(self.p['job'])
        self.ap['config_hash'] = a.digest(self.c)
        self.ap['prior_tts_audit']['historical_identity'] = a.historical_identity(self.l)

    def test_single_korean_job_and_prefix(self):
        plan = self.validate()
        self.assertEqual(plan['tts_characters'], 250)
        self.assertEqual(plan['day_total_reserved'], 1450)

    def test_happy_path_preserves_entire_history(self):
        r = FakeRuntime(self.l, self.c); self.run_fake(r)
        self.assertEqual(r.ledger['days'], self.l['days'])
        self.assertEqual(r.ledger['publications'][:-1], self.l['publications'])
        self.assertEqual(r.effects.count('review'), 1)
        self.assertEqual(r.effects.count('tts'), 1)
        self.assertEqual(r.effects.count('upload'), 1)
        self.assertEqual(r.ledger['additional_batches'][self.p['batch_id']]['status'], 'complete')

    def test_completed_retry_is_noop(self):
        r = FakeRuntime(self.l, self.c); first = self.run_fake(r); calls = r.effects[:]
        self.assertEqual(self.run_fake(r), first); self.assertEqual(r.effects, calls)

    def test_changed_approved_content_rejected(self):
        self.p['job']['title'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'content changed'): self.validate()

    def test_untrusted_approval_rejected(self):
        with self.assertRaisesRegex(ValueError, 'trusted approval'):
            a.validate(self.p, self.ap, '', self.l, self.c, DAY)

    def test_wrong_channel_rejected(self):
        self.p['job']['channel'] = 'korean'; self.resign()
        with self.assertRaisesRegex(ValueError, 'Korean channel'): self.validate()

    def test_stale_day_rejected(self):
        with self.assertRaisesRegex(ValueError, 'date'):
            a.validate(self.p, self.ap, a.digest(self.ap), self.l, self.c, '2026-11-07')

    def test_money_scope_missing_rejected(self):
        self.ap['cost_scope'].pop('variable_review_charges_accepted')
        with self.assertRaisesRegex(ValueError, 'cost authorization'): self.validate()

    def test_prior_usage_omission_rejected(self):
        self.ap['prior_tts_audit']['entries'] = []
        with self.assertRaisesRegex(ValueError, 'usage omitted'): self.validate()

    def test_prefix_undercount_rejected(self):
        self.ap['prior_tts_audit']['entries'][0]['characters'] = 243
        with self.assertRaisesRegex(ValueError, 'undercounts'): self.validate()

    def test_uncertain_attempts_counted(self):
        e = copy.deepcopy(self.ap['prior_tts_audit']['entries'][0]); e['attempt_id'] += '-uncertain'
        self.ap['prior_tts_audit']['entries'].append(e)
        self.assertEqual(self.validate()['prior_characters'], 1500)

    def test_aggregate_daily_cap(self):
        self.ap['prior_tts_audit']['entries'][0]['characters'] = 5800
        with self.assertRaisesRegex(ValueError, 'Aggregate'): self.validate()

    def test_reservation_counts_even_without_tts(self):
        self.l['additional_batches'] = {'another': {'production_date': DAY, 'status': 'complete',
                                                  'tts_reservation': 5500}}
        with self.assertRaisesRegex(ValueError, 'Aggregate'): self.validate()

    def test_unknown_reservation_failclosed(self):
        self.l['additional_batches'] = {'another': {'production_date': DAY, 'status': 'complete'}}
        with self.assertRaisesRegex(ValueError, 'Unknown'): self.validate()

    def test_topic_alias_dedup(self):
        self.p['event_keys'].append('old-story'); self.resign()
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.validate()

    def test_no_external_effect_before_first_cas(self):
        r = FakeRuntime(self.l, self.c); r.fail = 'cas-1'
        with self.assertRaises(RuntimeError): self.run_fake(r)
        self.assertEqual(r.effects, [])

    def test_failure_injections_never_replay(self):
        for failure in ('cas-2', 'review', 'checkpoint', 'cas-3', 'review_fail', 'media',
                        'cas-4', 'tts', 'cas-5', 'channel', 'cas-6', 'upload', 'cas-7'):
            with self.subTest(failure=failure):
                r = FakeRuntime(self.l, self.c); r.fail = failure
                with self.assertRaises((RuntimeError, ValueError)): self.run_fake(r)
                calls = r.effects[:]; r.fail = None
                with self.assertRaisesRegex(ValueError, 'already reserved'): self.run_fake(r)
                self.assertEqual(r.effects, calls)
                self.assertEqual(r.ledger['days'], self.l['days'])

    def test_low_balance_no_reservation(self):
        r = FakeRuntime(self.l, self.c); r.balance = 1
        with self.assertRaisesRegex(ValueError, 'Insufficient'): self.run_fake(r)
        self.assertEqual(r.writes, 0); self.assertEqual(r.effects, [])

    def test_midnight_after_review_no_tts(self):
        r = FakeRuntime(self.l, self.c); r.change_date = 'review'
        with self.assertRaisesRegex(ValueError, 'date expired'): self.run_fake(r)
        self.assertNotIn('tts', r.effects)

    def test_midnight_after_tts_no_upload(self):
        r = FakeRuntime(self.l, self.c); r.change_date = 'tts'
        with self.assertRaisesRegex(ValueError, 'date expired'): self.run_fake(r)
        self.assertNotIn('upload', r.effects)

    def test_midnight_after_upload_preserves_receipt(self):
        r = FakeRuntime(self.l, self.c); r.change_date = 'upload'; self.run_fake(r)
        self.assertEqual(r.ledger['additional_batches'][self.p['batch_id']]['status'], 'complete')
        self.assertEqual(len(r.ledger['publications']), 5)

    def test_unresolved_other_batch_blocks_new_work(self):
        self.l['additional_batches'] = {'other': {'production_date': DAY, 'status': 'review_submitted',
                                                 'tts_reservation': 250}}
        with self.assertRaisesRegex(ValueError, 'Another additional'): self.validate()

    def test_missing_original_publication_blocks(self):
        self.l['publications'].pop(); self.resign()
        with self.assertRaisesRegex(ValueError, 'receipt missing'): self.validate()

    def test_original_day_requires_four(self):
        self.l['days'][DAY]['jobs'].pop(); self.resign()
        with self.assertRaisesRegex(ValueError, 'four-job'): self.validate()

    def graphics_scope(self):
        self.p['media_policy'] = 'original_graphics'
        self.ap['cost_scope'].update(render_qa_requests=1, render_qa_model='gpt-5.4',
            render_qa_max_output_tokens=1200, render_qa_web_calls=0)
        self.resign()

    def test_graphics_needs_explicit_actual_video_qa_budget(self):
        self.p['media_policy'] = 'original_graphics'; self.resign()
        with self.assertRaisesRegex(ValueError, 'QA cost scope'): self.validate()

    def test_graphics_second_review_precedes_upload(self):
        self.graphics_scope(); r = FakeRuntime(self.l, self.c); self.run_fake(r)
        self.assertLess(r.effects.index('tts'), r.effects.index('render_qa'))
        self.assertLess(r.effects.index('render_qa'), r.effects.index('upload'))
        cp = r.ledger['additional_batches'][self.p['batch_id']]['checkpoints']['render_qa']
        self.assertEqual(cp['attempts'], 1); self.assertEqual(cp['status'], 'completed')

    def test_graphics_failed_or_uncertain_qa_never_uploads_or_replays(self):
        self.graphics_scope()
        for failure in ['render_qa', 'render_qa_fail']:
            r = FakeRuntime(self.l, self.c); r.fail = failure
            with self.assertRaises((ValueError, RuntimeError)): self.run_fake(r)
            self.assertNotIn('upload', r.effects)
            previous = r.effects[:]; r.fail = None
            with self.assertRaisesRegex(ValueError, 'already reserved'): self.run_fake(r)
            self.assertEqual(previous, r.effects)

    def test_approval_does_not_allow_four_jobs(self):
        self.ap['publication']['count'] = 4
        with self.assertRaisesRegex(ValueError, 'content approval'): self.validate()


if __name__ == '__main__': unittest.main()
