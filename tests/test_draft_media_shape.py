import json
import unittest
from unittest.mock import patch
import cloud_runner as c
from test_guards import batch


class DraftMediaShape(unittest.TestCase):
    """2026-11-13: the draft model returned scene.media as {"relevance": ...}.
    Draft media is advisory; a non-string value must be dropped, not crash as TypeError."""

    def test_object_media_is_dropped_not_a_schema_type_failure(self):
        for value in ({'relevance':'shows the product'},['a'],7,None,'not-in-library'):
            jobs=batch()
            for job in jobs:
                for scene in job['scenes']:scene['media']=value
            with self.subTest(value=value):
                c.validate_batch(jobs,'2026-11-05',set())
                self.assertTrue(all('media' not in s for j in jobs for s in j['scenes']))

    def test_guarded_validation_accepts_object_media_without_hold(self):
        jobs=batch()
        for job in jobs:
            for scene in job['scenes']:scene['media']={'relevance':'x'}
        ledger={'days':{'2026-11-05':{}},'publications':[]}
        with patch.object(c,'journal_put') as put,patch.object(c,'http') as network:
            result=c.guarded_draft_validation(ledger,'sha','2026-11-05',json.dumps({'jobs':jobs}),set())
        network.assert_not_called();put.assert_not_called()
        self.assertEqual(len(result),4)
        self.assertNotIn('retry_hold',ledger['days']['2026-11-05'])

    def test_download_ignores_non_string_media(self):
        jobs=batch()
        jobs[0]['scenes'][0]['media']={'relevance':'x'}
        with patch.object(c.p,'read',return_value={}):
            c.download_media(jobs)


if __name__=='__main__':unittest.main()
