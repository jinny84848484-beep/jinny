import copy, json, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import additional_batch as a, additional_expiry as x

LEDGER = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'state/ledger.json').read_text())
B = '2026-11-07-extra-01'

class Expiry(unittest.TestCase):
    def h(self, l): return a.digest(l['additional_batches'][B])

    def test_real_ledger_expires_and_unblocks(self):
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

if __name__ == '__main__':
    unittest.main()


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
