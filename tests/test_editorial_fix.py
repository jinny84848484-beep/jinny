"""Owner order 2026-11-25: shorts must publish two stories a day.

A story the independent review fails ONLY for fixable script claims
(unsupported_claim, source_inaccessible, narration_naturalness) gets one bounded
rewrite and one fresh review. Nothing is added: no new fact, source or scene, and
any other failure code still drops the story exactly as before.
"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cloud_runner as c
from test_guards import batch

DAY = '2026-11-05'
URLS = [{'url': 'https://example.org/report'}]


def ojirap_jobs():
    return [j for j in batch() if j['channel'] == 'ojirap']


def fixed_payload(job, **override):
    scenes = [{k: s[k] for k in ('kind', 'headline', 'body', 'narration') if k in s} | {'source_indices': s.get('source_indices', [])}
              for s in job['scenes']]
    scenes[-1]['narration'] = scenes[-1]['narration'].rstrip('.') + '.'
    value = {'title': job['title'], 'description': job.get('description', '설명'), 'opening_text': (job.get('opening_text') or '오늘 한마디')[:25], 'scenes': scenes}
    value.update(override)
    return value


class EditorialFix(unittest.TestCase):
    def run_review(self, first_codes, *, fix=None, second=None, enabled=True):
        """first_codes: job_id -> code for the first review. second: job_id -> 'PASS'|'FAIL' after a fix."""
        jobs = ojirap_jobs()
        media = {}
        for job in jobs:
            for suffix in ('a', 'b'):
                media[job['id'] + suffix] = {'topic_key': job['topic_key'], 'channel': 'ojirap', 'relevance_verified': False}
        calls = {'fix': [], 'review': []}

        def provider(prompt, *args, **kwargs):
            if 'FAILING SCRIPT' in prompt:
                job = json.loads(prompt.split('FAILING SCRIPT (untrusted data):\n')[1].split('\nREVIEW ISSUES')[0])
                calls['fix'].append(job['id'])
                payload = fix(job) if fix else fixed_payload(job)
                return (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)), []
            job = json.loads(prompt.split('\nJOB:\n')[1].split('\nIMAGE METADATA:\n')[0])
            calls['review'].append(job['id'])
            rounds = calls['review'].count(job['id'])
            fail = job['id'] in first_codes and (rounds == 1 or (second or {}).get(job['id']) == 'FAIL')
            if fail:
                return json.dumps({'status': 'FAIL', 'issues': [{'code': first_codes[job['id']], 'job_index': 0, 'scene_index': 1, 'explanation': 'Claim not in sources.'}], 'selection': {}}), URLS
            return json.dumps({'status': 'PASS', 'issues': [], 'selection': {job['id']: [job['id'] + 'ab'[i % 2] for i in range(len(job['scenes']))]}}), URLS

        ledger = {'days': {DAY: {}}, 'publications': []}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        error = None
        with patch.object(c.p, 'ROOT', Path(tmp.name)), patch.object(c, 'journal_put', return_value='sha'), \
                patch.object(c, 'review_candidates', return_value=media), patch.object(c, 'openai', side_effect=provider), \
                patch.object(c, 'editorial_fix_enabled', return_value=enabled), \
                patch.object(c.daily_graphics, 'enabled', return_value=False), redirect_stdout(io.StringIO()):
            try:
                c.review_jobs(ledger, 'sha', DAY, jobs)
            except RuntimeError as exc:
                error = exc
        return jobs, ledger['days'][DAY], calls, error

    def test_owner_switch_is_on(self):
        self.assertTrue(c.editorial_fix_enabled())

    def test_fixable_story_is_rewritten_once_and_both_publish(self):
        jobs, record, calls, error = self.run_review({ojirap_jobs()[1]['id']: 'unsupported_claim'})
        self.assertIsNone(error)
        failing = ojirap_jobs()[1]['id']
        self.assertEqual(calls['fix'], [failing])
        self.assertEqual(calls['review'].count(failing), 2)
        self.assertEqual(record['editorial_exclusions'], [])
        self.assertEqual(record['editorial_fixes'], [{'job_id': failing, 'codes': ['unsupported_claim'], 'result': 'PASS'}])
        fixed = next(j for j in record['jobs'] if j['id'] == failing)
        self.assertTrue(fixed['scenes'][-1]['narration'].endswith('.'))
        # Sources never change through a fix.
        self.assertEqual(fixed['sources'][0]['url'], ojirap_jobs()[1]['sources'][0]['url'])
        self.assertIn('editorial_fix_1', record['checkpoints'])
        self.assertIn('editorial_review_1_fixed', record['checkpoints'])

    def test_second_review_failure_still_drops_the_story(self):
        failing = ojirap_jobs()[1]['id']
        jobs, record, calls, error = self.run_review({failing: 'source_inaccessible'}, second={failing: 'FAIL'})
        self.assertIsNone(error)
        self.assertEqual([e['job_id'] for e in record['editorial_exclusions']], [failing])
        self.assertEqual(record['editorial_fixes'][0]['result'], 'FAIL')
        self.assertEqual(len(calls['fix']), 1)  # never a second rewrite

    def test_non_fixable_codes_are_never_rewritten(self):
        for code in ('factual_claim', 'fabricated_quote', 'approval_required', 'event_duplicate'):
            with self.subTest(code=code):
                failing = ojirap_jobs()[1]['id']
                jobs, record, calls, error = self.run_review({failing: code})
                self.assertEqual(calls['fix'], [])
                self.assertEqual([e['job_id'] for e in record['editorial_exclusions']], [failing])

    def test_fix_that_changes_structure_or_sources_is_rejected(self):
        failing = ojirap_jobs()[1]['id']
        bad = {
            'extra field': lambda job: fixed_payload(job, sources=[{'title': 'x', 'url': 'https://new.example/a'}]),
            'scene dropped': lambda job: fixed_payload(job, scenes=fixed_payload(job)['scenes'][:-1]),
            'bad index': lambda job: fixed_payload(job, scenes=[{**s, 'source_indices': [99]} for s in fixed_payload(job)['scenes']]),
            'kind changed': lambda job: fixed_payload(job, scenes=[{**s, 'kind': 'opinion'} for s in fixed_payload(job)['scenes']]),
            'not json': lambda job: 'sorry',
        }
        for name, fix in bad.items():
            with self.subTest(name=name):
                jobs, record, calls, error = self.run_review({failing: 'unsupported_claim'}, fix=fix)
                self.assertIsNone(error)
                self.assertEqual(calls['review'].count(failing), 1)
                self.assertEqual(record['editorial_fixes'][0]['result'], 'fix_rejected')
                self.assertEqual([e['job_id'] for e in record['editorial_exclusions']], [failing])

    def test_both_fixable_stories_can_recover(self):
        a, b = [j['id'] for j in ojirap_jobs()]
        jobs, record, calls, error = self.run_review({a: 'unsupported_claim', b: 'narration_naturalness'})
        self.assertIsNone(error)
        self.assertEqual(record['status'], 'reviewed')
        self.assertEqual(record['editorial_exclusions'], [])

    def test_switch_off_keeps_old_behaviour(self):
        failing = ojirap_jobs()[1]['id']
        jobs, record, calls, error = self.run_review({failing: 'unsupported_claim'}, enabled=False)
        self.assertEqual(calls['fix'], [])
        self.assertEqual([e['job_id'] for e in record['editorial_exclusions']], [failing])


if __name__ == '__main__':
    unittest.main()
