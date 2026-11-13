import copy
import json
import unittest
from unittest.mock import patch
import cloud_runner as c
from test_guards import batch
from test_source_manifest import manifest_for,evidence_for


def response(evidence=False,text='Researched text',status='completed'):
    output=[{'type':'message','content':[{'type':'output_text','text':text,'annotations':[]}]}]
    if evidence:
        output.append({'type':'web_search_call','action':{'sources':[{'url':'https://example.org/report','title':'Report'}]}})
    return {'status':status,'output':output}


class ResearchRecovery(unittest.TestCase):
    def test_completed_without_evidence_is_typed_and_sanitized(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',return_value=response(text='private provider text')):
            with self.assertRaises(c.CompletedResearchUnusable) as caught:
                c.openai('prompt',True)
        d=caught.exception.diagnostic
        self.assertEqual(d['reason'],'no_recognized_source_evidence')
        self.assertEqual(d['recognized_source_count'],0)
        self.assertNotIn('private',json.dumps(d))
        self.assertNotIn('secret',json.dumps(d))

    def test_completed_with_sources_but_empty_text_is_held(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',return_value=response(True,'  ')):
            with self.assertRaisesRegex(c.CompletedResearchUnusable,'empty_research_text'):
                c.openai('prompt',True)

    def test_usable_research_preserves_source_guard(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',return_value=response(True)):
            text,sources=c.openai('prompt',True)
        self.assertEqual(text,'Researched text')
        self.assertEqual(sources[0]['url'],'https://example.org/report')

    def test_plain_text_url_is_not_source_evidence(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',return_value=response(text='https://example.org/report')):
            with self.assertRaises(c.CompletedResearchUnusable):c.openai('prompt',True)

    def test_incomplete_response_remains_ambiguous(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',return_value=response(True,status='incomplete')):
            with self.assertRaisesRegex(RuntimeError,'Incomplete') as caught:c.openai('prompt',True)
        self.assertNotIsInstance(caught.exception,c.CompletedResearchUnusable)

    def test_known_failure_persists_hold_and_preserves_other_records(self):
        for stage in ['research','editorial_review']:
            ledger={'days':{'old':{'status':'complete'},'today':{'status':'research_submitted'}},'publications':[{'video_id':'existing'}]}
            before=copy.deepcopy(ledger)
            error=c.CompletedResearchUnusable('no_recognized_source_evidence',response(),[])
            with patch.object(c,'openai',side_effect=error),patch.object(c,'journal_put') as save:
                with self.assertRaises(c.CompletedResearchUnusable):c.guarded_research(ledger,'sha','today',stage,'prompt',True)
            save.assert_called_once_with(ledger,'sha')
            self.assertEqual(ledger['days']['old'],before['days']['old'])
            self.assertEqual(ledger['publications'],before['publications'])
            self.assertTrue(ledger['days']['today']['retry_hold'])
            self.assertEqual(ledger['days']['today']['research_failures'][0]['stage'],stage)

    def test_uncertain_network_error_does_not_reclassify_or_retry(self):
        ledger={'days':{'today':{'status':'research_submitted'}},'publications':[]}
        with patch.object(c,'openai',side_effect=RuntimeError('Network request failed')) as gen,patch.object(c,'journal_put') as save:
            with self.assertRaisesRegex(RuntimeError,'Network'):c.guarded_research(ledger,'sha','today','research','prompt',True)
            gen.assert_called_once();save.assert_not_called()
        self.assertEqual(ledger['days']['today']['status'],'research_submitted')

    def test_hold_blocks_all_downstream_calls_even_with_jobs(self):
        day=c.p.now().date().isoformat()
        for record in [{'status':'research_completed_unusable'}, {'status':'reviewed','retry_hold':True,'jobs':[{}]}]:
            ledger={'days':{day:record},'publications':[]}
            with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as preflight,patch.object(c,'collect_metrics') as metrics,patch.object(c,'openai') as gen,patch.object(c.media_renderer,'render') as render,patch.object(c,'upload') as upload,patch.object(c,'journal_put') as save:
                with self.assertRaisesRegex(RuntimeError,'hold'):c.produce()
                for call in [preflight,metrics,gen,render,upload,save]:call.assert_not_called()

    def test_durable_write_failure_cannot_enable_retry(self):
        ledger={'days':{'today':{'status':'research_submitted'}},'publications':[]}
        error=c.CompletedResearchUnusable('no_recognized_source_evidence',response(),[])
        with patch.object(c,'openai',side_effect=error) as gen,patch.object(c,'journal_put',side_effect=RuntimeError('write failed')):
            with self.assertRaisesRegex(RuntimeError,'write failed'):c.guarded_research(ledger,'sha','today','research','prompt',True)
            gen.assert_called_once()

    def test_produce_persists_completed_failure_before_any_render_or_upload(self):
        day=c.p.now().date().isoformat()
        for failure_stage in ['research','editorial_review']:
            with self.subTest(stage=failure_stage):
                ledger={'days':{},'publications':[{'job_id':'old','video_id':'existing'}]}
                saved=[]
                def save(value,sha):
                    saved.append(copy.deepcopy(value))
                    return 'new-sha'
                researched=response(True,json.dumps(manifest_for(batch())))
                researched['output'][-1]['action']['sources']=evidence_for(manifest_for(batch()))
                responses=[response()] if failure_stage=='research' else [researched,response(text=json.dumps({'jobs':[job for job in batch() if job['channel']=='ojirap']})),response()]
                with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true','OPENAI_API_KEY':'test'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'journal_put',side_effect=save),patch.object(c,'preflight'),patch.object(c,'collect_metrics'),patch.object(c.p,'collect',return_value={'path':'unused'}),patch.object(c.p,'read',return_value={}),patch.object(c,'validate_batch'),patch.object(c.relevant_media,'candidates',return_value={}),patch.object(c,'http',side_effect=responses) as network,patch.object(c.media_renderer,'render') as render,patch.object(c,'upload') as upload:
                    with self.assertRaises(c.CompletedResearchUnusable):c.produce()
                    self.assertEqual(network.call_count,len(responses))
                    render.assert_not_called();upload.assert_not_called()
                self.assertEqual(saved[0]['days'][day]['status'],'research_submitted')
                self.assertEqual(saved[-1]['days'][day]['status'],'research_completed_unusable')
                self.assertEqual(saved[-1]['days'][day]['research_failures'][0]['stage'],'editorial_review_0' if failure_stage=='editorial_review' else failure_stage)
                self.assertTrue(saved[-1]['days'][day]['retry_hold'])
                self.assertEqual(saved[-1]['publications'],[{'job_id':'old','video_id':'existing'}])

