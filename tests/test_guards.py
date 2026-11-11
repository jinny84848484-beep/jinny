import copy
import unittest
from unittest.mock import patch
import cloud_runner as c

def batch():
    jobs=[]
    for i,(channel,category) in enumerate(zip(['korean','korean','ojirap','ojirap'],['technology','culture','technology','culture'])):
        narration=('One Korean perspective explains this current event with verified facts and original personal commentary. ' * 2) if channel=='korean' else ('오늘의 소식을 확인하고 제 생각을 덧붙입니다. ' * 3)
        jobs.append({'id':f'2026-11-05-test-{i}','channel':channel,'category':category,'topic_key':f'unique-event-{i%2}','title':'Test title','sources':[{'title':'A','url':'https://example.com/news/a'},{'title':'B','url':'https://example.org/news/b'}],'scenes':[{'kind':'fact','headline':'Test','body':'Text','narration':narration,'source_indices':[0,1]} for _ in range(4)]})
    for job in jobs:
        packet={'topic_key':job['topic_key'],'category':job['category'],
                'summary':'Dated current event '+job['topic_key'],
                'verified_facts':[{'text':'Verified event '+job['topic_key'],'source_indices':[0,1]}],
                'uncertainties':[], 'sources':[{**source,'role':'primary' if i==0 else 'independent_reporting','accessed':True} for i,source in enumerate(job['sources'])]}
        job.update(format_version=c.PAIRED_FORMAT,fact_packet=packet,fact_packet_hash=c.fingerprint(packet))
    return jobs

class Guards(unittest.TestCase):
    def test_direct_web_page_read_counts_as_source_evidence(self):
        result={'output':[{'type':'web_search_call','action':{'type':'open_page','url':'https://example.org/report'}},{'type':'message','content':[{'type':'output_text','annotations':[{'type':'url_citation','url':'https://example.org/report','title':'Report'}]}]}]}
        self.assertEqual([s['url'] for s in c.research_sources(result)],['https://example.org/report'])

    def test_batch_accepts_two_topics_in_two_languages(self):
        c.validate_batch(batch(),'2026-11-05',set())

    def test_duplicate_topic_stops(self):
        jobs=batch();jobs[3]['topic_key']=jobs[0]['topic_key']
        with self.assertRaisesRegex(ValueError,'Duplicate topic'):c.validate_batch(jobs,'2026-11-05',set())

    def test_recent_topic_stops(self):
        with self.assertRaisesRegex(ValueError,'Duplicate topic'):c.validate_batch(batch(),'2026-11-05',{'unique-event-0'})

    def test_wrong_channel_distribution_stops(self):
        jobs=batch();jobs[0]['channel']='ojirap'
        with self.assertRaisesRegex(ValueError,'Two videos'):c.validate_batch(jobs,'2026-11-05',set())

    def test_no_secrets_no_network(self):
        with patch.dict(c.os.environ,{},clear=True),patch.object(c,'http') as network:
            with self.assertRaisesRegex(RuntimeError,'Missing configuration'):c.preflight()
            network.assert_not_called()

    def test_completed_batch_never_generates_again(self):
        day=c.p.now().date().isoformat()
        with patch.object(c,'preflight'),patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=({'days':{day:{'status':'complete'}},'publications':[]},'sha')),patch.object(c,'openai') as gen:
            c.produce();gen.assert_not_called()

    def test_ambiguous_submission_never_repeats(self):
        day=c.p.now().date().isoformat()
        for status in ['research_submitted','tts_submitted','rendered','upload_submitted']:
            with self.subTest(status=status),patch.object(c,'preflight'),patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=({'days':{day:{'status':status}},'publications':[]},'sha')),patch.object(c,'openai') as gen,patch.object(c.media_renderer,'render') as render:
                with self.assertRaisesRegex(RuntimeError,'reconcile'):c.produce()
                gen.assert_not_called();render.assert_not_called()

    def test_wrong_oauth_identity_stops(self):
        with patch.object(c,'http',return_value={'items':[{'id':'wrong-channel'}]}):
            with self.assertRaisesRegex(RuntimeError,'configured target'):c.verify_channel('ojirap','dummy')

if __name__=='__main__':unittest.main()
