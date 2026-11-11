import unittest
from unittest.mock import patch
import cloud_runner as c
from test_guards import batch

class CategorySlots(unittest.TestCase):
    def test_slots_are_distinct_and_balanced(self):
        self.assertEqual(len(c.BATCH_SLOTS),4)
        self.assertEqual(len({category for channel,category in c.BATCH_SLOTS}),2)
        self.assertEqual(sorted(channel for channel,category in c.BATCH_SLOTS),['korean','korean','ojirap','ojirap'])

    def test_wrong_assignment_rejected_even_with_distinct_categories(self):
        jobs=batch();jobs[0]['category']='film'
        with self.assertRaisesRegex(ValueError,'slots mismatch'):c.validate_batch(jobs,'2026-11-05',set(),slots=c.BATCH_SLOTS)

    def test_default_categories_can_change_daily(self):
        jobs=batch()
        for job in jobs:
            job['category']='film' if job['topic_key']=='unique-event-0' else 'music'
            job['fact_packet']['category']=job['category'];job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
        c.validate_batch(jobs,'2026-11-05',set())

    def test_order_does_not_change_slot_validity(self):
        c.validate_batch(list(reversed(batch())),'2026-11-05',set())

    def test_prompts_have_consistent_slots_and_language(self):
        for prompt in [c.PROMPT,c.RESEARCH_PROMPT,c.REVIEW_PROMPT]:
            self.assertIn(c.SLOT_INSTRUCTIONS,prompt)
            self.assertIn('korean means ENGLISH',prompt)
            self.assertIn('ojirap means KOREAN',prompt)
        self.assertIn('Headlines, body, description and sources do not count',c.PROMPT)
        self.assertIn('100-110 total words',c.PROMPT)
        self.assertIn('280-340 total characters',c.PROMPT)

    def test_short_narration_still_rejected_not_padded(self):
        jobs=batch()
        for scene in jobs[2]['scenes']:scene['narration']='짧음'
        with self.assertRaisesRegex(ValueError,'Korean narration length'):c.validate_batch(jobs,'2026-11-05',set())

    def test_slot_failure_does_not_invoke_length_correction(self):
        import json
        jobs=batch();jobs[0]['category']='unknown'
        ledger={'days':{'2026-11-05':{'status':'research_submitted'}}}
        with patch.object(c,'correct_narration_lengths_once') as correction,patch.object(c,'journal_put'):
            with self.assertRaisesRegex(RuntimeError,'unknown_category'):c.guarded_draft_validation(ledger,'sha','2026-11-05',json.dumps({'jobs':jobs}),set(),allow_length_correction=True)
            correction.assert_not_called()
        self.assertTrue(ledger['days']['2026-11-05']['retry_hold'])

