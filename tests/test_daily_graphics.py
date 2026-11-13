import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cloud_runner as c
import daily_graphics as g
from test_guards import batch

DAY = '2026-11-05'
URLS = [{'url': 'https://example.org/report'}]


def photos(jobs):
    media = {}
    for job in jobs:
        for suffix in ('a', 'b'):
            media[job['id'] + suffix] = {'topic_key': job['topic_key'], 'channel': job['channel'], 'relevance_verified': False}
    return media


def job_of(prompt):
    return json.loads(prompt.split('\nJOB:\n', 1)[1].split('\nIMAGE METADATA:\n', 1)[0])


class Disabled(unittest.TestCase):
    def test_flag_is_off_by_default_and_changes_nothing(self):
        self.assertIs(c.p.cfg().get('daily_graphics_fallback'), False)
        self.assertFalse(g.enabled())
        self.assertEqual(c.research_instructions(), c.RESEARCH_PROMPT)
        with patch.object(c.relevant_media, 'candidates', side_effect=RuntimeError('Not enough relevant licensed photo candidates; batch held')):
            with self.assertRaisesRegex(RuntimeError, 'batch held'):
                c.review_candidates(batch())


class Cards(unittest.TestCase):
    def test_cards_show_only_reviewed_scene_text_with_notice(self):
        for job in batch():
            for i, scene in enumerate(job['scenes']):
                data, labels = g.render_card(job, i)
                self.assertEqual(data, g.render_card(job, i)[0])  # deterministic
                drawn = ' '.join(label['text'] for label in labels)
                text = g.card_text(job, i)
                self.assertIn(text['notice'], drawn)
                self.assertTrue(all(b >= a for a, b in zip(g.SAFE[:2], labels[0]['bbox'][:2])))
                for label in labels:
                    x0, y0, x1, y1 = label['bbox']
                    self.assertTrue(g.SAFE[0] <= x0 and g.SAFE[1] <= y0 and x1 <= g.SAFE[2] and y1 <= g.SAFE[3])
                words = set((scene['headline'] + ' ' + scene.get('body', '')).split())
                content = [label['text'] for label in labels][1:]
                fixed = {text['label'], text['footer'], text['notice'], text['counter']}
                for line in content:
                    if line in fixed:
                        continue
                    self.assertTrue(set(line.split()) <= words, line)

    def test_maximum_length_korean_and_english_fit(self):
        jobs = batch()
        jobs[2]['scenes'][0].update(headline='가' * 45, body='나다라 ' * 21)
        jobs[0]['scenes'][0].update(headline='W' * 45, body=('Wide words ' * 8)[:85])
        g.render_card(jobs[2], 0)
        g.render_card(jobs[0], 0)

    def test_control_characters_and_missing_headline_are_rejected(self):
        job = batch()[0]
        job['scenes'][0]['headline'] = 'bad‮text'
        with self.assertRaises(ValueError):
            g.render_card(job, 0)
        job['scenes'][0]['headline'] = ''
        with self.assertRaises(ValueError):
            g.render_card(job, 0)

    def test_publish_regenerates_missing_card_and_rejects_altered_bytes(self):
        job = batch()[0]
        with tempfile.TemporaryDirectory() as directory, patch.object(c.p, 'ROOT', Path(directory)):
            entries = g.build(job)
            for i, scene in enumerate(job['scenes']):
                scene['media'] = g.media_key(job, i)
            first = Path(directory) / entries[g.media_key(job, 0)]['path']
            first.unlink()
            g.ensure([job], entries)
            self.assertTrue(first.is_file())
            first.write_bytes(b'not the reviewed card')
            with self.assertRaisesRegex(ValueError, 'differ'):
                g.ensure([job], entries)
            job['scenes'][0]['media'], job['scenes'][1]['media'] = job['scenes'][1]['media'], job['scenes'][0]['media']
            with self.assertRaisesRegex(ValueError, 'wrong scene'):
                g.ensure([job], entries)


class Enabled(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(g, 'enabled', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_review(self, jobs, provider, media):
        ledger = {'days': {DAY: {}}, 'publications': []}
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        with patch.object(c.p, 'ROOT', Path(self.dir.name)), patch.object(c, 'journal_put', return_value='sha'), \
                patch.object(c, 'review_candidates', return_value=media), patch.object(c, 'openai', side_effect=provider) as api, \
                redirect_stdout(io.StringIO()):
            c.review_jobs(ledger, 'sha', DAY, jobs)
        return ledger, api

    def test_research_keeps_fact_rules_and_drops_photo_readiness(self):
        text = c.research_instructions()
        self.assertTrue(text.startswith(c.RESEARCH_PROMPT))
        self.assertIn('Photo availability alone must NOT make a story unready', text)
        self.assertIn('Never relax factual, source independence or primary-source requirements', text)

    def test_one_story_without_photos_no_longer_holds_the_batch(self):
        jobs = [job for job in batch() if job['channel']=='ojirap']
        def fake(group):
            if group[0]['topic_key'] == jobs[0]['topic_key']:
                raise RuntimeError('Not enough relevant licensed photo candidates; batch held')
            return {k: v for k, v in photos(group).items()}
        with patch.object(c.relevant_media, 'candidates', side_effect=fake):
            media = c.review_candidates(jobs)
        self.assertTrue(media)
        self.assertFalse(any(v['topic_key'] == jobs[0]['topic_key'] for v in media.values()))

    def test_story_without_photos_passes_with_graphics_after_full_review(self):
        jobs = [job for job in batch() if job['channel']=='ojirap']
        media = {k: v for k, v in photos(jobs).items() if v['topic_key'] != jobs[0]['topic_key']}
        seen = []
        def provider(prompt, *args, **kwargs):
            job = job_of(prompt)
            seen.append((job['id'], args[2], kwargs['instructions']))
            own = [k for k, v in media.items() if v['topic_key'] == job['topic_key'] and v['channel'] == job['channel']]
            keys = [g.media_key(job, i) for i in range(len(job['scenes']))] if not own else (own * 4)[:len(job['scenes'])]
            return json.dumps({'status': 'PASS', 'issues': [], 'selection': {job['id']: keys}}), URLS
        ledger, api = self.run_review(jobs, provider, media)
        self.assertEqual(api.call_count, 2)
        record = ledger['days'][DAY]
        self.assertEqual(record['status'], 'reviewed')
        graphic_jobs = {job['id'] for job in jobs if job['topic_key'] == jobs[0]['topic_key']}
        for job in record['jobs']:
            uses = {record['media'][s['media']].get('policy') for s in job['scenes']}
            self.assertEqual(uses, {g.POLICY} if job['id'] in graphic_jobs else {None})
        for job_id, images, instructions in seen:
            if job_id in graphic_jobs:
                self.assertEqual(images, {})  # nothing fake is sent as a photo
                self.assertIn('No photographs are attached', instructions)
            else:
                self.assertIn('report only media issue codes', instructions)

    def test_media_only_failure_switches_that_story_to_graphics(self):
        jobs = [job for job in batch() if job['channel']=='ojirap']
        media = photos(jobs)
        def provider(prompt, *args, **kwargs):
            job = job_of(prompt)
            if job['id'] == jobs[1]['id']:
                return json.dumps({'status': 'FAIL', 'issues': [{'code': 'media_relevance', 'job_index': 0, 'scene_index': 1, 'explanation': 'Photo shows a different product.'}], 'selection': {}}), URLS
            return json.dumps({'status': 'PASS', 'issues': [], 'selection': {job['id']: [job['id'] + 'a', job['id'] + 'b'] * 2}}), URLS
        ledger, _ = self.run_review(jobs, provider, media)
        record = ledger['days'][DAY]
        self.assertEqual(record['status'], 'reviewed')
        switched = next(job for job in record['jobs'] if job['id'] == jobs[1]['id'])
        self.assertTrue(all(s['media'] == g.media_key(switched, i) for i, s in enumerate(switched['scenes'])))
        details = json.loads((Path(self.dir.name) / 'runs' / DAY / 'checkpoints' / 'editorial-details.json').read_text())
        self.assertEqual(details['decisions'][1]['replaced_media_decision']['issues'][0]['code'], 'media_relevance')

    def test_factual_issue_still_holds_even_with_media_issue(self):
        jobs = [job for job in batch() if job['channel']=='ojirap']
        media = photos(jobs)
        fail = json.dumps({'status': 'FAIL', 'issues': [
            {'code': 'media_relevance', 'job_index': 0, 'scene_index': 1, 'explanation': 'Wrong photo.'},
            {'code': 'factual_claim', 'job_index': 0, 'scene_index': 2, 'explanation': 'The date is contradicted by the source.'}], 'selection': {}})
        with self.assertRaisesRegex(RuntimeError, 'batch held'):
            self.run_review(jobs, lambda *a, **k: (fail, URLS), media)

    def test_invalid_graphics_selection_is_a_contract_failure(self):
        jobs = [job for job in batch() if job['channel']=='ojirap']
        def provider(prompt, *args, **kwargs):
            job = job_of(prompt)
            return json.dumps({'status': 'PASS', 'issues': [], 'selection': {job['id']: ['invented-a', 'invented-b', 'invented-a', 'invented-b']}}), URLS
        with self.assertRaisesRegex(RuntimeError, 'batch held'):
            self.run_review(jobs, provider, {})


if __name__ == '__main__':
    unittest.main()
