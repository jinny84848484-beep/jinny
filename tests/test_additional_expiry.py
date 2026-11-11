import copy, json, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import additional_batch as a, additional_expiry as x

B = '2026-11-07-extra-01'
LEDGER = {
    'days': {},
    'publications': [{'job_id': 'prior-job', 'video_id': 'priorVid001', 'channel': 'ojirap', 'topic_key': 'prior-event'}],
    'additional_batches': {B: {
        'status': 'review_completed', 'production_date': '2026-11-07',
        'job_id': '2026-11-07-fixture-extra', 'event_keys': ['fixture-extra-event'],
        'checkpoints': {'review': {'status': 'completed'}},
    }},
    'additional_sessions': {'fixture-run': {'batch_id': B, 'status': 'waiting'}},
}

class Expiry(unittest.TestCase):
    def h(self, l): return a.digest(l['additional_batches'][B])

    def test_review_completed_fixture_expires_and_unblocks(self):
        new = x.expire_stale_batch(LEDGER, B, today='2026-11-10', expected_record_hash=self.h(LEDGER))
        self.assertTrue(all(x.batch_resolved(r) for r in new['additional_batches'].values()))
        self.assertFalse(any(s['status'] == 'waiting' for s in new['additional_sessions'].values()))
        prior = new['additional_batches'][B]['expiry']['previous_record']
        self.assertEqual(prior, LEDGER['additional_batches'][B])   # audit kept intact
        self.assertEqual(LEDGER['additional_batches'][B]['status'], 'review_completed')  # input untouched

    def test_refuses_same_day(self):
        with self.assertRaises(Exception):
            x.expire_stale_batch(LEDGER, B, today='2026-11-07', expected_record_hash=self.h(LEDGER))

    def test_refuses_wrong_hash(self):
        with self.assertRaises(Exception):
            x.expire_stale_batch(LEDGER, B, today='2026-11-10', expected_record_hash='0' * 64)

    def test_refuses_after_tts(self):
        l = copy.deepcopy(LEDGER); l['additional_batches'][B]['checkpoints']['tts'] = {'status': 'submitted'}
        with self.assertRaises(Exception):
            x.expire_stale_batch(l, B, today='2026-11-10', expected_record_hash=self.h(l))

    def test_refuses_if_published(self):
        l = copy.deepcopy(LEDGER); l['publications'].append({'additional_batch_id': B})
        with self.assertRaises(Exception):
            x.expire_stale_batch(l, B, today='2026-11-10', expected_record_hash=self.h(l))

    def test_tampered_expiry_not_resolved(self):
        new = x.expire_stale_batch(LEDGER, B, today='2026-11-10', expected_record_hash=self.h(LEDGER))
        new['additional_batches'][B]['expiry']['previous_record']['checkpoints']['tts'] = {}
        self.assertFalse(x.batch_resolved(new['additional_batches'][B]))

    def test_scheduled_guard_now_passes(self):
        import cloud_runner as c
        with self.assertRaises(RuntimeError):
            c.assert_no_unresolved_additional(LEDGER)
        new = x.expire_stale_batch(LEDGER, B, today='2026-11-10', expected_record_hash=self.h(LEDGER))
        c.assert_no_unresolved_additional(new)



class MetricsNonBlocking(unittest.TestCase):
    def test_metrics_failure_does_not_stop_production(self):
        import io, cloud_runner as c
        from contextlib import redirect_stderr
        from unittest.mock import patch
        ledger = {'days': {}, 'publications': [], 'additional_batches': {}}
        with patch.dict(c.os.environ, {'PUBLIC_UPLOADS_ENABLED': 'true'}), \
             patch.object(c, 'journal_get', return_value=(ledger, 'sha')), \
             patch.object(c, 'preflight'), \
             patch.object(c, 'collect_metrics', side_effect=RuntimeError('www.googleapis.com HTTP 500')), \
             patch.object(c.p, 'collect', side_effect=RuntimeError('reached research')), \
             redirect_stderr(io.StringIO()) as err:
            with self.assertRaisesRegex(RuntimeError, 'reached research'):
                c.produce()
        self.assertIn('Metrics skipped', err.getvalue())


class ExternalUpload(unittest.TestCase):
    def test_external_upload_resolves_and_records_publication(self):
        h = a.digest(LEDGER['additional_batches'][B])
        new = x.resolve_external_upload(LEDGER, B, today='2026-11-10', expected_record_hash=h,
                                        video_id='4B18u-hUPkw', channel='ojirap', channel_id='UCZj1FlWDcoiEQNLfpwc5qtw')
        rec = new['additional_batches'][B]
        self.assertEqual(rec['status'], x.EXTERNAL)
        self.assertTrue(x.batch_resolved(rec))
        self.assertEqual(new['publications'][-1]['video_id'], '4B18u-hUPkw')
        self.assertFalse(any(s['status'] == 'waiting' for s in new['additional_sessions'].values()))
        import cloud_runner as c
        c.assert_no_unresolved_additional(new)

    def test_resolved_external_record_is_terminal_and_cannot_be_expired_again(self):
        resolved=x.resolve_external_upload(LEDGER,B,today='2026-11-10',expected_record_hash=a.digest(LEDGER['additional_batches'][B]),video_id='fixture0001',channel='ojirap',channel_id='fixture-channel')
        before=copy.deepcopy(resolved)
        self.assertTrue(x.batch_resolved(resolved['additional_batches'][B]))
        with self.assertRaisesRegex(ValueError,'review_completed'):
            x.expire_stale_batch(resolved,B,today='2026-11-11',expected_record_hash=a.digest(resolved['additional_batches'][B]))
        self.assertEqual(resolved,before)

    def test_external_rejects_bad_id_and_duplicates(self):
        h = a.digest(LEDGER['additional_batches'][B])
        with self.assertRaises(Exception):
            x.resolve_external_upload(LEDGER, B, today='2026-11-10', expected_record_hash=h,
                                      video_id='bad', channel='ojirap', channel_id='x')
        dup = LEDGER['publications'][0]['video_id']
        with self.assertRaises(Exception):
            x.resolve_external_upload(LEDGER, B, today='2026-11-10', expected_record_hash=h,
                                      video_id=dup, channel='ojirap', channel_id='x')


if __name__ == '__main__':
    unittest.main()
