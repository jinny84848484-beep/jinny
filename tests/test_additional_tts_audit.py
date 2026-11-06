import datetime as dt
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import additional_tts_audit as audit

DAY = '2026-11-06'
NOW = dt.datetime(2026, 11, 6, 16, tzinfo=dt.timezone.utc)


class ReadOnlyAuditTests(unittest.TestCase):
    def test_korea_day_boundaries_seconds_and_milliseconds(self):
        self.assertEqual(audit.day_window(DAY, NOW), (1793890800, 1793977200, True))
        now = dt.datetime(2026, 11, 6, 13, tzinfo=dt.timezone.utc)
        self.assertFalse(audit.day_window(DAY, now)[2])

    def test_generation_or_general_post_is_never_allowed(self):
        with patch.object(audit.urllib.request, 'build_opener') as opener:
            for method, path in [('POST', '/v1/text-to-speech/test'), ('POST', '/v1/history'),
                                 ('DELETE', '/v1/history/item'), ('GET', '/v1/history/item/audio')]:
                with self.assertRaises(ValueError): audit.audit_request(method, path)
            opener.assert_not_called()

    def fixture(self, identity='private-id', stamp=1793890801):
        return {'history_item_id': identity, 'date_unix': stamp, 'character_count_change_from': 100,
                'character_count_change_to': 500, 'model_id': 'eleven_v4',
                'text': '[calm] private narration', 'voice_name': 'PRIVATE VOICE', 'request_id': 'PRIVATE REQUEST'}

    def test_history_is_paginated_and_raw_fields_discarded(self):
        calls = []
        pages = [{'history': [self.fixture()], 'has_more': True, 'last_history_item_id': 'private-id'},
                 {'history': [self.fixture('private-id2', 1793890802)], 'has_more': False}]
        def request(method, path, **kw):
            calls.append((method, path, kw)); return pages.pop(0)
        summary = audit.history_summary(request, 1793890800, 1793977200)
        self.assertTrue(summary['pagination_complete']); self.assertFalse(summary['billed_input_characters_verified'])
        self.assertEqual(summary['history_counter_delta_sum'], 800)
        self.assertEqual(calls[1][2]['query']['start_after_history_item_id'], 'private-id')
        serialized = json.dumps(summary)
        for secret in ['private-id', 'private narration', 'PRIVATE VOICE', 'PRIVATE REQUEST']:
            self.assertNotIn(secret, serialized)

    def test_duplicate_cursor_and_page_cap_do_not_claim_completeness(self):
        def duplicate(*args, **kw): return {'history': [self.fixture()], 'has_more': True, 'last_history_item_id': 'id'}
        with self.assertRaises(ValueError): audit.history_summary(duplicate, 1793890800, 1793977200)
        with patch.object(audit, 'MAX_PAGES', 1):
            self.assertFalse(audit.history_summary(duplicate, 1793890800, 1793977200)['pagination_complete'])

    def test_readonly_collection_does_not_promote_history_to_paid_cap(self):
        calls=[]
        def request(method, path, **kw):
            calls.append((method,path,kw))
            if path.endswith('subscription'):
                return {'character_count': 100, 'character_limit': 10000, 'next_character_count_reset_unix': 1796500000,
                        'invoice': 'PRIVATE'}
            if path.endswith('history'): return {'history': [self.fixture()], 'has_more': False}
            return {'columns': ['timestamp', 'credits', 'voice_name'], 'column_units': ['milliseconds','credits',None],
                    'rows': [[1793890800000, 400, 'PRIVATE VOICE']]}
        result = audit.collect(DAY, now=NOW, request=request)
        self.assertFalse(result['includes_all_attempts_and_uncertain_submissions'])
        self.assertFalse(result['production_authorized']); self.assertFalse(result['analytics']['credits_verified'])
        body = next(kw['body'] for method,path,kw in calls if method == 'POST')
        self.assertEqual(body['start_time'], 1793890800000); self.assertEqual(body['end_time'], 1793977200000)
        self.assertNotIn('PRIVATE',json.dumps(result)); self.assertNotIn('private narration',json.dumps(result))

    def test_unknown_schema_and_denied_calls_are_unknown_not_zero(self):
        def request(*args, **kwargs): raise RuntimeError('SECRET raw response')
        result = audit.collect(DAY, now=NOW, request=request)
        self.assertEqual(result['history']['status'], 'unavailable_or_incomplete')
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertFalse(audit.analytics_summary({'rows': []})['credits_verified'])

    def test_audit_workflow_has_no_production_credentials_or_write_permission(self):
        source=(Path(__file__).parents[1]/'.github/workflows/additional-tts-audit.yml').read_text()
        self.assertIn('contents: read',source); self.assertNotIn('contents: write',source)
        for key in ('OPENAI_API_KEY','YOUTUBE_OJIRAP_OAUTH','YOUTUBE_KOREAN_OAUTH','additional_driver.py'):
            self.assertNotIn(key,source)
        self.assertIn('python additional_tts_audit.py',source)
        self.assertNotIn('schedule:',source); self.assertIn('path: private-artifacts/tts-audit.enc.json',source)

if __name__=='__main__':unittest.main()
