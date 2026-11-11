import copy
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import additional_batch as a
import additional_expiry as expiry
import cloud_runner as c
import ledger_health as h
from test_additional_expiry import B, LEDGER


def terminal_fixture():
    ledger = expiry.resolve_external_upload(
        LEDGER, B, today='2026-11-10',
        expected_record_hash=a.digest(LEDGER['additional_batches'][B]),
        video_id='fixture0001', channel='ojirap', channel_id='fixture-channel')
    ledger['additional_batches']['completed-fixture'] = {'status': 'complete'}
    expired = expiry.expire_stale_batch(
        LEDGER, B, today='2026-11-10',
        expected_record_hash=a.digest(LEDGER['additional_batches'][B]))
    ledger['additional_batches']['expired-fixture'] = expired['additional_batches'][B]
    return ledger


class LedgerHealth(unittest.TestCase):
    def test_all_three_terminal_states_pass_actual_production_guard(self):
        ledger = terminal_fixture()
        guard = Mock(wraps=c.assert_no_unresolved_additional)
        report = h.inspect_ledger(ledger, guard)
        self.assertEqual(report['status'], 'healthy')
        self.assertEqual(report['batch_count'], 3)
        self.assertEqual(report['unresolved_count'], 0)
        self.assertTrue(report['guard_passed'])
        guard.assert_called_once_with(ledger)

    def test_unresolved_fixture_reports_operational_not_unit_failure(self):
        report = h.inspect_ledger(copy.deepcopy(LEDGER), c.assert_no_unresolved_additional)
        self.assertEqual(report['status'], 'unresolved')
        self.assertEqual(report['message'], '운영 ledger에 미해결 배치 있음')
        self.assertEqual(report['unresolved_count'], 1)
        self.assertFalse(report['guard_passed'])

    def test_tampered_terminal_and_malformed_records_fail_closed(self):
        for record in (None, {'status': 'unknown'}, {'status': expiry.EXTERNAL, 'expiry': {}}):
            ledger = terminal_fixture()
            ledger['additional_batches']['bad'] = record
            self.assertEqual(h.inspect_ledger(ledger, c.assert_no_unresolved_additional)['status'], 'unresolved')
        ledger = terminal_fixture()
        ledger['additional_batches'][B]['expiry']['previous_record_hash'] = '0' * 64
        self.assertEqual(h.inspect_ledger(ledger, c.assert_no_unresolved_additional)['status'], 'unresolved')

    def test_guard_rejection_cannot_be_hidden_by_terminal_labels(self):
        guard = Mock(side_effect=RuntimeError('guard rejected'))
        report = h.inspect_ledger(terminal_fixture(), guard)
        self.assertEqual(report['status'], 'unresolved')
        self.assertEqual(report['unresolved_count'], 0)
        self.assertFalse(report['guard_passed'])

    def test_input_and_history_are_unchanged(self):
        for ledger in (copy.deepcopy(LEDGER), terminal_fixture()):
            before = copy.deepcopy(ledger)
            h.inspect_ledger(ledger, c.assert_no_unresolved_additional)
            self.assertEqual(ledger, before)

    def test_invalid_root_schema_fails_without_calling_guard(self):
        for ledger in (None, [], {}, {'additional_batches': []}):
            guard = Mock()
            self.assertEqual(h.inspect_ledger(ledger, guard)['status'], 'invalid')
            guard.assert_not_called()

    def run_file(self, raw, *, guard=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_bytes(raw)
            output = io.StringIO()
            with redirect_stdout(output):
                code = h.main(['--ledger', str(path)], guard=guard)
            self.assertEqual(path.read_bytes(), raw)
            return code, json.loads(output.getvalue())

    def test_explicit_file_pass_has_hash_and_no_production_effect(self):
        raw = json.dumps(terminal_fixture()).encode()
        with ExitStack() as stack:
            effects = [stack.enter_context(patch.object(c, name, side_effect=AssertionError(name)))
                       for name in ('journal_get', 'journal_put', 'http', 'preflight', 'produce', 'openai', 'upload')]
            effects += [stack.enter_context(patch.object(c.p, 'synthesize', side_effect=AssertionError('tts'))),
                        stack.enter_context(patch.object(c.media_renderer, 'render', side_effect=AssertionError('render')))]
            code, report = self.run_file(raw)
            self.assertEqual(code, 0)
            self.assertEqual(report['ledger_sha256'], hashlib.sha256(raw).hexdigest())
            for effect in effects:
                effect.assert_not_called()

    def test_explicit_unresolved_file_has_distinct_exit_code(self):
        code, report = self.run_file(json.dumps(LEDGER).encode(), guard=c.assert_no_unresolved_additional)
        self.assertEqual(code, 1)
        self.assertEqual(report['message'], h.UNRESOLVED)

    def test_invalid_json_has_distinct_exit_code_without_raw_data(self):
        code, report = self.run_file(b'private invalid content', guard=Mock())
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'invalid')
        self.assertNotIn('private', json.dumps(report))

    def test_concurrent_file_change_is_reported_without_overwrite(self):
        raw = json.dumps(terminal_fixture()).encode()
        with patch.object(Path, 'read_bytes', side_effect=[raw, raw + b' ']), redirect_stdout(io.StringIO()) as output:
            code = h.main(['--ledger', 'fixture.json'], guard=c.assert_no_unresolved_additional)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())['message'], h.CHANGED)

    def test_production_guard_restores_environment(self):
        with patch.dict(h.os.environ, {'SHORTS_STATE_DIR': 'fixture-before'}):
            before = h.sys.dont_write_bytecode
            with h.production_guard() as guard:
                self.assertIs(guard, c.assert_no_unresolved_additional)
                self.assertNotEqual(h.os.environ['SHORTS_STATE_DIR'], 'fixture-before')
            self.assertEqual(h.os.environ['SHORTS_STATE_DIR'], 'fixture-before')
            self.assertEqual(h.sys.dont_write_bytecode, before)


if __name__ == '__main__':
    unittest.main()
