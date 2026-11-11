import copy,json,unittest
from unittest.mock import patch
import cloud_runner as c
from test_guards import batch

def manifest_for(jobs):
    stories=[];seen=set()
    for job in jobs:
        if job['topic_key'] in seen:continue
        seen.add(job['topic_key'])
        packet=copy.deepcopy(job['fact_packet'])
        stories.append({**packet,'ready':True,'visual_subject':'Exact named person','visual_queries':['Exact named person portrait','Exact named person photograph']})
    return {'stories':stories}

def evidence_for(manifest):return [{'url':s['url']} for story in manifest['stories'] for s in story['sources']]

class SourceManifest(unittest.TestCase):
    def setUp(self):self.jobs=batch();self.manifest=manifest_for(self.jobs)

    def test_ready_research_requires_observed_specific_independent_sources(self):
        self.assertEqual(c.parse_research_manifest(json.dumps(self.manifest),evidence_for(self.manifest),set()),self.manifest)
        mutations=[lambda m:m['stories'][0].update(ready=False),lambda m:m['stories'][0]['sources'][1].update(url='https://example.com/news/another'),lambda m:m['stories'][0]['sources'][1].update(url='https://example.org/'),lambda m:m['stories'][0]['sources'][1].update(accessed=False)]
        for mutate in mutations:
            value=copy.deepcopy(self.manifest);mutate(value)
            with self.assertRaises(ValueError):c.parse_research_manifest(json.dumps(value),evidence_for(value),set())
        with self.assertRaisesRegex(ValueError,'evidence_missing'):c.parse_research_manifest(json.dumps(self.manifest),[],set())
        value=copy.deepcopy(self.manifest)
        for source in value['stories'][0]['sources']:source['role']='primary'
        with self.assertRaisesRegex(ValueError,'independent_missing'):c.parse_research_manifest(json.dumps(value),evidence_for(value),set())

    def test_dropped_source_restored_and_reordered_indices_remapped_without_changing_narration(self):
        before=copy.deepcopy(self.jobs)
        self.jobs[0]['sources']=[self.jobs[0]['sources'][1]]
        for scene in self.jobs[0]['scenes']:scene['source_indices']=[0]
        fixed=c.bind_researched_sources(self.jobs,self.manifest)
        self.assertEqual(fixed[0]['sources'],before[0]['sources'])
        self.assertTrue(all(scene['source_indices']==[1] for scene in fixed[0]['scenes']))
        self.assertEqual([s['narration'] for s in fixed[0]['scenes']],[s['narration'] for s in before[0]['scenes']])
        self.assertEqual(len(self.jobs[0]['sources']),1)

    def test_unrelated_search_result_homepage_and_changed_identity_cannot_fill_gap(self):
        for url in ('https://unrelated.example/report','https://example.org/'):
            jobs=copy.deepcopy(self.jobs);jobs[0]['sources'][1]['url']=url
            with self.assertRaisesRegex(ValueError,'invented citation'):c.bind_researched_sources(jobs,self.manifest)
        jobs=copy.deepcopy(self.jobs);jobs[0]['topic_key']='other-event'
        with self.assertRaisesRegex(ValueError,'topic mismatch'):c.bind_researched_sources(jobs,self.manifest)

    def test_unready_research_is_held_before_any_draft_request(self):
        ledger={'days':{'day':{}}};self.manifest['stories'][0]['ready']=False
        with patch.object(c,'journal_put') as save,patch.object(c,'openai') as api:
            with self.assertRaisesRegex(RuntimeError,'research_manifest_unready'):c.guarded_research_manifest(ledger,'sha','day',json.dumps(self.manifest),evidence_for(self.manifest),set())
            api.assert_not_called();save.assert_called_once()
        self.assertEqual(ledger['days']['day']['status'],'research_validation_failed')

    def test_tracking_annotation_differences_do_not_drop_real_sources(self):
        for story in self.manifest['stories']:
            for source in story['sources']:source['url']+='?utm_source=openai'
        c.parse_research_manifest(json.dumps(self.manifest),evidence_for(manifest_for(self.jobs)),set())
        fixed=c.bind_researched_sources(self.jobs,self.manifest)
        self.assertEqual(len(fixed[0]['sources']),2)

    def test_unready_packet_is_not_misreported_as_duplicate_topic(self):
        self.manifest['stories'][1]['topic_key']=self.manifest['stories'][0]['topic_key']
        self.manifest['stories'][1]['ready']=False
        with self.assertRaisesRegex(ValueError,'research_manifest_unready'):c.parse_research_manifest(json.dumps(self.manifest),evidence_for(self.manifest),set())
        self.manifest['stories'][1]['ready']=True
        with self.assertRaisesRegex(ValueError,'research_manifest_topics'):c.parse_research_manifest(json.dumps(self.manifest),evidence_for(self.manifest),set())

    def test_commons_photo_metadata_cannot_count_as_independent_news(self):
        for host in ('commons.wikimedia.org','en.wikipedia.org'):
            value=copy.deepcopy(self.manifest)
            value['stories'][0]['sources'][1].update(url='https://'+host+'/wiki/File:Person.jpg',role='original_reporting')
            with self.assertRaisesRegex(ValueError,'research_manifest_news_source'):c.parse_research_manifest(json.dumps(value),evidence_for(value),set())
