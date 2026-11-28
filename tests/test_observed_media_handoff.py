import io
import json
import unittest
from unittest.mock import patch

import cloud_runner as c
import relevant_media as m
from test_guards import batch
from test_source_manifest import evidence_for, manifest_for

FILE_PAGES = [
    "https://commons.wikimedia.org/wiki/File:Sungshin_Women%27s_Univ._Station_Ticket_Barrier_1.jpg",
    "https://commons.wikimedia.org/wiki/File:Seoul_Metro_Line_2_train_interior_13_Jan_2024.jpg",
]
TITLES = [
    "File:Sungshin Women's Univ. Station Ticket Barrier 1.jpg",
    "File:Seoul Metro Line 2 train interior 13 Jan 2024.jpg",
]


class ObservedMediaHandoff(unittest.TestCase):
    def setUp(self):
        self.jobs = [job for job in batch() if job["channel"] == "ojirap"]
        self.manifest = manifest_for(self.jobs)
        self.manifest["stories"][0]["visual_file_pages"] = FILE_PAGES
        self.evidence = evidence_for(self.manifest) + [{"url": url} for url in FILE_PAGES]

    def bound_job(self, evidence):
        parsed = c.parse_research_manifest(json.dumps(self.manifest), evidence, set())
        return c.bind_researched_sources(self.jobs, parsed)[0]

    def test_observed_pair_survives_empty_search_into_visual_review(self):
        job = self.bound_job(self.evidence)
        self.assertEqual(job["research_visual_titles"], TITLES)
        self.assertTrue(all(src["url"] not in FILE_PAGES for src in job["sources"]))
        pages = {str(i): {"pageid": i, "title": title, "imageinfo": [{
            "mime": "image/jpeg", "width": 4010, "height": 3010,
            "url": "https://upload.wikimedia.org/station-" + str(i) + ".jpg",
            "descriptionurl": url, "extmetadata": {"LicenseShortName": {"value": "CC0"}},
        }]} for i, (title, url) in enumerate(zip(TITLES, FILE_PAGES), 1)}
        with patch.object(m.urllib.request, "urlopen", return_value=io.BytesIO(
            json.dumps({"query": {"pages": pages}}).encode()
        )), patch.object(m, "search", return_value={}) as search, patch.object(
            m, "search_related_category", return_value={}
        ):
            media = c.review_candidates([job], photo_only=True)
        self.assertEqual({item["title"] for item in media.values()}, set(TITLES))
        self.assertTrue(all(item["illustrative_only"] and not item["relevance_verified"]
                            for item in media.values()))
        search.assert_not_called()

    def test_unobserved_or_non_file_hints_are_discarded_and_cannot_be_drafted(self):
        self.jobs[0]["research_visual_titles"] = TITLES
        self.jobs[0]["visual_source_titles"] = TITLES
        job = self.bound_job(self.evidence[:-1])
        self.assertNotIn("research_visual_titles", job)
        self.assertNotIn("visual_source_titles", job)
        self.manifest["stories"][0]["visual_file_pages"] = [FILE_PAGES[0], "https://other.example/wiki/File:Two.jpg"]
        self.assertNotIn("research_visual_titles", self.bound_job(self.evidence))

    def test_only_bound_exact_subject_preserves_original_archive_notice(self):
        self.manifest["stories"][0]["visual_file_pages"] = [
            {"url": FILE_PAGES[0], "relationship": "exact_subject"},
            {"url": FILE_PAGES[1], "relationship": "illustrative"},
        ]
        self.jobs[0]["research_direct_titles"] = TITLES
        item = {"media_type": "image", "notice": "자료사진 · 2024", "relevance_verified": False}
        job = self.bound_job(self.evidence)
        self.assertEqual(job["research_direct_titles"], [TITLES[0]])
        with patch.object(m, "resolve_titles", return_value={
            "a": dict(item, title=TITLES[0]), "b": dict(item, title=TITLES[1]),
        }):
            media = c.review_candidates([job], photo_only=True)
        self.assertEqual(media["a"]["notice"], item["notice"])
        self.assertFalse(media["a"]["relevance_verified"])
        self.assertTrue(media["b"]["illustrative_only"])

    def test_one_rejected_exact_file_uses_standard_search_and_keeps_label(self):
        job = self.bound_job(self.evidence)
        item = {"media_type": "image", "relevance_verified": False}
        with patch.object(m, "resolve_titles", return_value={"hint": item}), patch.object(
            m, "search", side_effect=[{"hint": item, "new": item}, {}]
        ), patch.object(m, "search_related_category", return_value={}):
            media = c.review_candidates([job], photo_only=True)
        self.assertEqual(set(media), {"hint", "new"})
        self.assertTrue(media["hint"]["illustrative_only"])
        self.assertNotIn("illustrative_only", media["new"])
