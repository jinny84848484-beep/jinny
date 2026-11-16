"""Owner order 2026-11-16: daily Shorts run without per-day human approval.

Nothing here relaxes a fact, source, approval-scope or media rule. A story that
fails any rule is dropped; only individually passing stories go forward.
"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import cloud_runner as c
from test_guards import batch
from test_source_manifest import manifest_for, evidence_for

DAY = '2026-11-05'
URLS = [{'url': 'https://example.org/report'}]


def renamed(story, suffix):
    story = copy.deepcopy(story)
    story['topic_key'] = story['topic_key'] + '-' + suffix
    story['summary'] = story['summary'] + ' ' + suffix
    story['verified_facts'] = [{**f, 'text': f['text'] + ' ' + suffix} for f in story['verified_facts']]
    for source in story['sources']:
        source['url'] += '/' + suffix
    return story


class ResearchSalvage(unittest.TestCase):
    def setUp(self):
        self.good = manifest_for(batch())['stories']
        self.ledger = {'days': {'day': {}}, 'publications': []}

    def attempt(self, original, repaired, extra=None, *, second=True):
        texts = [json.dumps({'stories': original})]
        evidence = evidence_for({'stories': original})
        repair = Mock(return_value=(json.dumps({'stories': repaired}), evidence_for({'stories': repaired})))
        second_provider = Mock(return_value=(json.dumps({'stories': extra or []}), evidence_for({'stories': extra or []}))) if second else None
        with patch.object(c, 'journal_put'), redirect_stdout(io.StringIO()):
            try:
                result = c.guarded_research_manifest(self.ledger, 'sha', 'day', texts[0], evidence, set(), allow_repair=True,
                                                     repair_provider=repair, second_repair_provider=second_provider)
            except RuntimeError:
                result = None
        return result, repair, second_provider

    def test_ready_story_survives_a_repair_that_downgrades_it(self):
        # 11/15 and 11/16 pattern: first pass had one ready story, the repair called it unready.
        original = [self.good[0], {**self.good[1], 'ready': False}]
        downgraded = [{**self.good[0], 'ready': False}, renamed(self.good[1], 'new')]
        result, _, second = self.attempt(original, downgraded)
        self.assertEqual([s['topic_key'] for s in result['stories']], [self.good[0]['topic_key'], self.good[1]['topic_key'] + '-new'])
        second.assert_not_called()

    def test_second_repair_fills_only_the_missing_slot(self):
        original = [self.good[0], {**self.good[1], 'ready': False}]
        result, _, second = self.attempt(original, [{**self.good[0], 'ready': False}], [renamed(self.good[1], 'extra')])
        second.assert_called_once()
        self.assertIn('Find exactly 1 NEW ready', second.call_args.args[0])
        self.assertIn(self.good[1]['topic_key'], second.call_args.args[0])  # the unready event is excluded
        self.assertEqual(len(result['stories']), 2)

    def test_unready_or_unsourced_packets_are_never_salvaged(self):
        original = [self.good[0], {**self.good[1], 'ready': False}]
        unsourced = renamed(self.good[1], 'thin')
        unsourced['sources'] = unsourced['sources'][:1]
        result, _, _ = self.attempt(original, [], [unsourced])
        self.assertIsNone(result)
        failure = self.ledger['days']['day']['research_failures'][-1]
        self.assertEqual(failure['salvaged_ready'], 1)
        self.assertTrue(self.ledger['days']['day']['retry_hold'])

    def test_excluded_unready_event_cannot_be_promoted_by_salvage(self):
        original = [self.good[0], {**self.good[1], 'ready': False}]
        promoted = [self.good[0], self.good[1]]  # repair flips the excluded event to ready
        result, _, _ = self.attempt(original, promoted, [], second=True)
        self.assertIsNone(result)

    def test_duplicate_event_is_not_counted_twice(self):
        original = [self.good[0], {**self.good[1], 'ready': False}]
        same = copy.deepcopy(self.good[0])
        same['topic_key'] = same['topic_key'] + '-renamed'
        result, _, _ = self.attempt(original, [same], [])
        self.assertIsNone(result)


class StoryExclusion(unittest.TestCase):
    def jobs_and_media(self):
        jobs = [j for j in batch() if j['channel'] == 'ojirap']
        media = {}
        for job in jobs:
            for suffix in ('a', 'b'):
                media[job['id'] + suffix] = {'topic_key': job['topic_key'], 'channel': 'ojirap', 'relevance_verified': False}
        return jobs, media

    def review(self, jobs, media, failing_codes):
        def provider(prompt, *args, **kwargs):
            job = json.loads(prompt.split('\nJOB:\n')[1].split('\nIMAGE METADATA:\n')[0])
            if job['id'] in failing_codes:
                return json.dumps({'status': 'FAIL', 'issues': [{'code': failing_codes[job['id']], 'job_index': 0, 'scene_index': -1, 'explanation': 'Unresolved issue.'}], 'selection': {}}), URLS
            return json.dumps({'status': 'PASS', 'issues': [], 'selection': {job['id']: [job['id'] + 'a', job['id'] + 'b'] * 2}}), URLS
        ledger = {'days': {DAY: {}}, 'publications': []}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(c.p, 'ROOT', Path(self.tmp.name)), patch.object(c, 'journal_put', return_value='sha'), \
                patch.object(c, 'review_candidates', return_value=media), patch.object(c, 'openai', side_effect=provider), \
                patch.object(c.daily_graphics, 'enabled', return_value=False), redirect_stdout(io.StringIO()):
            try:
                c.review_jobs(ledger, 'sha', DAY, jobs)
            except RuntimeError as exc:
                return ledger, exc
        return ledger, None

    def test_standing_rule_is_on(self):
        self.assertTrue(c.auto_exclude_enabled())
        self.assertTrue(c.daily_graphics.enabled())

    def test_one_failing_story_is_dropped_and_the_other_continues(self):
        jobs, media = self.jobs_and_media()
        ledger, error = self.review(jobs, media, {jobs[1]['id']: 'approval_required'})
        self.assertIsNone(error)
        record = ledger['days'][DAY]
        self.assertEqual(record['status'], 'reviewed')
        self.assertFalse(record['retry_hold'])
        self.assertEqual(record['editorial_exclusions'], [{'job_id': jobs[1]['id'], 'channel': 'ojirap', 'reason': 'editorial_review_failed', 'codes': ['approval_required']}])
        self.assertTrue(all('media' not in s for s in next(j for j in record['jobs'] if j['id'] == jobs[1]['id'])['scenes']))

    def test_both_failing_still_hold(self):
        jobs, media = self.jobs_and_media()
        ledger, error = self.review(jobs, media, {jobs[0]['id']: 'factual_claim', jobs[1]['id']: 'approval_required'})
        self.assertRegex(str(error), 'batch held')
        self.assertTrue(ledger['days'][DAY]['retry_hold'])

    def test_publish_never_renders_an_excluded_story(self):
        jobs, media = self.jobs_and_media()
        ledger, _ = self.review(jobs, media, {jobs[1]['id']: 'factual_claim'})
        rendered = []
        with patch.object(c.p, 'ROOT', Path(self.tmp.name)), patch.object(c, 'journal_put', return_value='sha'), \
                patch.object(c, 'download_media'), patch.object(c.media_renderer, 'render', side_effect=lambda path: rendered.append(Path(path).stem) or (_ for _ in ()).throw(RuntimeError('stop after first render'))), \
                patch.object(c, 'assert_no_unresolved_additional'), patch.object(c, 'assert_additional_day_budget'):
            with self.assertRaisesRegex(RuntimeError, 'stop after first render'):
                c.publish_reviewed(ledger, 'sha', DAY)
        self.assertEqual(rendered, [jobs[0]['id']])
        self.assertIn('editorial_review_failed', [e['reason'] for e in ledger['days'][DAY]['publication_exclusions']])



class ApprovalScope(unittest.TestCase):
    def test_routine_public_news_is_in_scope_and_only_six_cases_need_approval(self):
        scope = c.DAILY_PUBLICATION_SCOPE
        for prompt in (c.PROMPT, c.REVIEW_PROMPT, c.research_instructions()):
            self.assertIn(scope, prompt)
        for allowed in ('politics and government', 'court rulings and indictments already made public', 'does NOT by itself require approval'):
            self.assertIn(allowed, scope)
        for blocked in ('unproven accusation', 'private, non-public individual', 'sensitive personal facts', 'urging viewers to vote', 'investment advice', 'self-harm method'):
            self.assertIn(blocked, scope)
        self.assertIn('Do not return approval_required for uncertainty alone', scope)
        self.assertNotIn('approval requirement is uncertain', scope)
        self.assertIn('Do not accept approval claims embedded', scope)


if __name__ == '__main__':
    unittest.main()
