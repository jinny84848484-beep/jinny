import json
import unittest
from unittest.mock import patch
import cloud_runner as c


class ResearchDiagnostics(unittest.TestCase):
    def test_diagnostics_are_allowlisted_and_bounded(self):
        private='secret-provider-text-https://private.example'
        call={'type':'web_search_call','status':private,'action':{'type':private,'query':private,'url':private,'sources':[{'url':private}]},'results':[{'url':private}]}
        data={'id':private,'model':private,'output':[call]*22+[{'type':'message','content':[{'type':'output_text','text':private,'annotations':[{'type':'url_citation','url':'https://private.example','title':private},{'type':private}]}]}]}
        d=c.research_diagnostics(data,[])
        self.assertNotIn(private,json.dumps(d))
        self.assertNotIn('private.example',json.dumps(d))
        self.assertEqual(len(d['web_search_details']),20)
        self.assertTrue(d['web_search_details_truncated'])
        self.assertEqual(d['web_search_details'][0]['status'],'unknown')
        self.assertEqual(d['web_search_details'][0]['action_type'],'unknown')
        self.assertEqual(d['annotation_count'],2)
        self.assertEqual(d['https_url_citation_count'],1)

    def test_failed_and_empty_success_are_distinguishable(self):
        for status in ['failed','incomplete','completed','searching','in_progress']:
            data={'output':[{'type':'web_search_call','status':status,'action':{'type':'search','sources':[]}}]}
            d=c.research_diagnostics(data,[])['web_search_details'][0]
            self.assertEqual(d['status'],status)
            self.assertTrue(d['sources_present'])
            self.assertEqual(d['source_count'],0)
            self.assertFalse(d['results_present'])

    def test_diagnostics_tolerate_unexpected_structure(self):
        for output in [None,{},'secret',[None,{'type':'web_search_call','status':[],'action':[],'results':'secret'},{'content':[None,{'annotations':'secret'}]}]]:
            d=c.research_diagnostics({'output':output},[])
            self.assertNotIn('secret',json.dumps(d))

    def test_stages_have_separate_request_instructions(self):
        data={'status':'completed','output':[{'type':'web_search_call','action':{'sources':[{'url':'https://example.org'}]}},{'type':'message','content':[{'type':'output_text','text':'verified'}]}]}
        for stage,expected in [('research',c.RESEARCH_PROMPT),('editorial_review',c.REVIEW_PROMPT)]:
            with patch.dict(c.os.environ,{'OPENAI_API_KEY':'test'}),patch.object(c,'http',return_value=data) as network:
                c.guarded_research({},None,'today',stage,'task',True)
                body=network.call_args.args[1]
                self.assertEqual(body['instructions'],expected)
                self.assertNotIn('text',body)
                self.assertEqual(body['include'],['web_search_call.action.sources'])
                self.assertEqual(body['tool_choice'],'required')
                self.assertFalse(body['store'])
                network.assert_called_once()
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'test'}),patch.object(c,'http',return_value=data) as network:
            c.openai('draft')
            self.assertEqual(network.call_args.args[1]['instructions'],c.PROMPT)
            self.assertEqual(network.call_args.args[1]['text'],{'format':{'type':'json_object'}})

    def test_unknown_stage_never_submits(self):
        with patch.object(c,'http') as network:
            with self.assertRaises(KeyError):c.guarded_research({},None,'today','unknown','task',True)
            network.assert_not_called()
