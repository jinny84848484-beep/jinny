import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import additional_runtime as ar
import additional_batch as a


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = ar.Runtime('a' * 40)
        self.runtime.bind_authorization({'config_hash': a.digest(self.runtime.config())},
                                        {'production_date': self.runtime.today()})
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(ar.c.p, 'ROOT', self.root); self.patch.start(); self.addCleanup(self.patch.stop)
        (self.root / 'assets').mkdir()
        self.job = {'id': '2026-11-06-extra-01-incident', 'channel': 'ojirap', 'topic_key': 'incident',
                    'scenes': [{'kind': 'fact', 'narration': 'fact', 'source_indices': [0, 1]} for _ in range(4)],
                    'sources': [{'url': 'https://reporter-a.org/article'}, {'url': 'https://reporter-b.org/article'}]}
        self.media = {}
        for index in range(2):
            key = 'commons-' + str(index) + '-12345678'
            raw = ('test-image-' + str(index)).encode(); path = 'assets/' + key + '.jpg'
            (self.root / path).write_bytes(raw)
            self.media[key] = {'topic_key': 'incident', 'media_type': 'image', 'license': 'CC0',
                               'credit': 'Artist', 'notice': '자료사진', 'title': 'File:Subject.jpg',
                               'path': path, 'url': 'https://upload.wikimedia.org/subject.jpg',
                               'source': 'https://commons.wikimedia.org/wiki/File:Subject.jpg',
                               'sha256': hashlib.sha256(raw).hexdigest()}
        self.packet = {'job': self.job, 'media': self.media}

    def response(self):
        keys = list(self.media)
        decision = {'status': 'PASS', 'issues': [], 'selection': {self.job['id']: keys * 2},
                    'source_checks': [{'url': source['url'], 'accessed': True,
                                       'verified_scene_indices': [0, 1, 2, 3],
                                       'independence_group': 'publisher-' + str(i)}
                                      for i, source in enumerate(self.job['sources'])]}
        return {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed',
             'action': {'type': 'open_page', 'url': source['url']}} for source in self.job['sources']] + [
             {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(decision)}]}]}

    def test_actual_adapter_requires_opened_sources(self):
        result = self.runtime.apply_review(self.packet, self.response())
        self.assertEqual(result['job']['editorial_status'], 'verified')
        self.assertTrue(all(s['article_read_this_review'] for s in result['job']['sources']))
        self.assertTrue(all(m['relevance_verified'] for m in result['media'].values()))

    def test_search_citation_cannot_impersonate_opened_article(self):
        response = self.response()
        response['output'] = response['output'][-1:]
        response['output'][0]['content'][0]['annotations'] = [{'type': 'url_citation', 'url': 'https://example.com'}]
        with self.assertRaisesRegex(ValueError, 'open-page evidence'):
            self.runtime.apply_review(self.packet, response)

    def test_incomplete_or_search_only_web_action_rejected(self):
        for field, value in [('status', 'in_progress'), ('action', {'type': 'search', 'url': self.job['sources'][0]['url']})]:
            response = self.response(); response['output'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'open-page evidence'):
                self.runtime.apply_review(self.packet, response)

    def test_independence_and_fact_coverage_required(self):
        for variant in ('same_publisher', 'uncovered_fact'):
            response = self.response(); part = response['output'][-1]['content'][0]
            d = json.loads(part['text'])
            for check in d['source_checks']:
                if variant == 'same_publisher': check['independence_group'] = 'same'
                else: check['verified_scene_indices'] = [0, 1]
            part['text'] = json.dumps(d)
            with self.assertRaisesRegex(ValueError, 'coverage incomplete'):
                self.runtime.apply_review(self.packet, response)

    def test_real_review_request_has_one_model_and_bounds(self):
        with patch.object(ar.c, 'review_image', return_value='data:image/jpeg;base64,AA=='):
            request = self.runtime.review_request(self.packet, a.MODEL, a.REVIEW_OUTPUT_TOKENS, a.REVIEW_TOOL_CALLS)
        self.assertEqual(request['model'], 'gpt-5.4'); self.assertEqual(request['max_tool_calls'], 3)
        self.assertEqual(request['max_output_tokens'], 2600); self.assertEqual(request['tool_choice'], 'required')
        self.assertEqual(len([c for c in request['input'][0]['content'] if c['type'] == 'input_image']), 2)
        with patch.dict(ar.os.environ, {'OPENAI_API_KEY': 'test-only'}), patch.object(ar.c, 'http', return_value=self.response()) as http:
            self.runtime.review(request)
            self.assertEqual(http.call_count, 1)

    def test_review_failure_has_no_provider_retry(self):
        with patch.dict(ar.os.environ, {'OPENAI_API_KEY': 'test-only'}), patch.object(ar.c, 'http', side_effect=RuntimeError('uncertain')) as http:
            with self.assertRaises(RuntimeError): self.runtime.review({})
            self.assertEqual(http.call_count, 1)

    def test_media_hash_change_rejected(self):
        self.runtime.validate_media(self.packet)
        (self.root / next(iter(self.media.values()))['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'bytes missing or changed'): self.runtime.validate_media(self.packet)

    def test_symlink_outside_root_rejected_before_read(self):
        path = self.root / next(iter(self.media.values()))['path']; path.unlink()
        path.symlink_to(self.root.parent / 'outside-test-file-that-need-not-exist')
        with self.assertRaisesRegex(ValueError, 'Unsafe media path'): self.runtime.validate_media(self.packet)

    def test_credit_balance_unavailable_rejected(self):
        with patch.object(ar.c.p, 'subscription', return_value={}):
            with self.assertRaisesRegex(ValueError, 'balance unavailable'): self.runtime.remaining_characters()

    def test_metered_terms_are_not_assumed_from_balance(self):
        with self.assertRaisesRegex(ValueError, 'terms unavailable'):
            self.runtime.assert_tts_budget({'tts_payment': {'mode': 'metered', 'pricing_evidence_hash': 'a' * 64}}, 300)

    def test_metered_price_terms_have_explicit_total_and_currency(self):
        terms = {'provider': 'ElevenLabs', 'currency': 'USD', 'model_id': 'eleven_v4',
                 'valid_on': self.runtime.today(), 'existing_metered_account': True,
                 'includes_all_taxes_fees_and_model_multipliers': True,
                 'max_cost_per_character': '0.001'}
        self.runtime.verified_tts_terms = terms
        costs = {'tts_payment': {'mode': 'metered', 'currency': 'USD', 'max_total': '0.50',
                                'max_cost_per_character': '0.001', 'pricing_evidence_hash': a.digest(terms)}}
        self.runtime.assert_tts_budget(costs, 300)
        with self.assertRaisesRegex(ValueError, 'exceeds approved'):
            self.runtime.assert_tts_budget(costs, 501)
        self.runtime.verified_tts_terms['valid_on'] = '1900-01-01'
        with self.assertRaisesRegex(ValueError, 'terms unavailable'):
            self.runtime.assert_tts_budget(costs, 300)

    def test_synthesis_guard_receives_actual_prefix_and_blocks_before_api(self):
        import pipeline as p
        job = {'id': 'test-job', 'channel': 'ojirap'}
        config = {'daily_character_cap': 6000, 'model_id': 'eleven_v4',
                  'channels': {'ojirap': {'voice_id': 'saved-voice', 'language': 'ko', 'delivery': 'calm'}}}
        folder = self.root / 'tts-test'; folder.mkdir()
        seen = []
        def guard(actual_job, speech, actual_config):
            self.assertEqual(actual_config, config)
            seen.append((actual_job, speech))
            raise ValueError('No spending permission')
        with patch.object(p, 'STATE', self.root), patch.object(p, 'cfg', return_value=config), \
             patch.object(p, 'validate', return_value='approved narration'), patch.object(p, 'api') as api:
            with self.assertRaisesRegex(ValueError, 'No spending permission'):
                p.synthesize(job, folder, billing_guard=guard)
            api.assert_not_called()
        self.assertEqual(seen, [(job, '[calm] approved narration')])

    def test_daily_synthesis_default_still_blocks_overage(self):
        import pipeline as p
        config = {'daily_character_cap': 6000, 'model_id': 'eleven_v4',
                  'channels': {'ojirap': {'voice_id': 'saved-voice', 'language': 'ko'}}}
        folder = self.root / 'tts-default'; folder.mkdir()
        with patch.object(p, 'STATE', self.root), patch.object(p, 'cfg', return_value=config), \
             patch.object(p, 'validate', return_value='approved narration'), patch.object(p, 'api') as api, \
             patch.object(p, 'subscription', return_value={'character_limit': 0, 'character_count': 0}):
            with self.assertRaisesRegex(RuntimeError, 'Insufficient included'):
                p.synthesize({'id': 'test-job', 'channel': 'ojirap'}, folder)
            api.assert_not_called()

    def test_billing_date_and_approved_config_rechecked(self):
        costs = {'tts_payment': {'mode': 'included'}}
        with patch.object(self.runtime, 'remaining_characters', return_value=10000):
            self.runtime.assert_tts_budget(costs, 300)
            with patch.object(self.runtime, 'today', return_value='1900-01-01'):
                with self.assertRaisesRegex(ValueError, 'date expired'):
                    self.runtime.assert_tts_budget(costs, 300)
            with patch.object(self.runtime, 'config', return_value={'changed': 'voice'}):
                with self.assertRaisesRegex(ValueError, 'config changed'):
                    self.runtime.assert_tts_budget(costs, 300)

    def test_balance_request_crossing_midnight_stops_synthesis(self):
        original_day = self.runtime.production_date
        day = [original_day]
        def balance():
            day[0] = '1900-01-01'
            return 10000
        with patch.object(self.runtime, 'today', side_effect=lambda: day[0]), \
             patch.object(self.runtime, 'remaining_characters', side_effect=balance):
            with self.assertRaisesRegex(ValueError, 'expired during balance'):
                self.runtime.assert_tts_budget({'tts_payment': {'mode': 'included'}}, 300)

    def test_final_video_qa_needs_exact_completed_pass(self):
        for result in ({'status': 'FAIL', 'issues': ['overlap']}, {'status': 'PASS', 'issues': ['unclear']},
                       {'status': 'PASS', 'issues': [], 'extra': True}):
            response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}
            with self.assertRaisesRegex(ValueError, 'did not pass'): self.runtime.assert_render_qa(response)
        response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': '{"status":"PASS","issues":[]}'}]}]}
        self.runtime.assert_render_qa(response)
        response['status'] = 'incomplete'
        with self.assertRaisesRegex(ValueError, 'incomplete'): self.runtime.assert_render_qa(response)

    def test_context_requires_manual_run_and_matching_head(self):
        env = {'GITHUB_REPOSITORY': ar.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ATTEMPT': '1', 'PUBLIC_UPLOADS_ENABLED': 'true',
               'GITHUB_SHA': 'a' * 40, 'ADDITIONAL_CONCURRENCY_GROUP': 'shorts-production'}
        with patch.dict(ar.os.environ, env): self.runtime.assert_context()
        for key, value in [('GITHUB_EVENT_NAME', 'schedule'), ('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_SHA', 'b' * 40)]:
            with patch.dict(ar.os.environ, {**env, key: value}):
                with self.assertRaisesRegex(ValueError, 'context required'): self.runtime.assert_context()

if __name__ == '__main__': unittest.main()
