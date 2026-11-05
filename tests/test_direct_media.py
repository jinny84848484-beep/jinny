import copy
import io
import json
import unittest
import urllib.parse
from unittest.mock import patch
import relevant_media as m
import staged_runner as s
from test_staged_runner import valid_jobs,DAY

class DirectMedia(unittest.TestCase):
    def payload(self):
        pages={}
        for i,title in enumerate(['File:One.jpg','File:Two.png','File:Unrequested.jpg'],1):
            pages[str(i)]={'pageid':i,'title':title,'imageinfo':[{'mime':'image/jpeg','width':1000,'height':800,'url':'https://upload.wikimedia.org/test.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/'+title,'extmetadata':{'LicenseShortName':{'value':'CC BY 4.0'},'Artist':{'value':'Test author'}}}]}
        return {'query':{'pages':pages}}

    def test_exact_titles_use_fixed_api_and_retain_unverified_media(self):
        payload=self.payload()
        with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as request:
            result=m.resolve_titles(['File:One.jpg','File:Two.png'],'event','ojirap')
        self.assertEqual(len(result),2)
        self.assertEqual({v['title'] for v in result.values()},{'File:One.jpg','File:Two.png'})
        self.assertTrue(all(v['relevance_verified'] is False for v in result.values()))
        query=urllib.parse.parse_qs(urllib.parse.urlparse(request.call_args.args[0].full_url).query)
        self.assertEqual(query['titles'],['File:One.jpg|File:Two.png'])
        self.assertNotIn('generator',query)

    def test_all_license_size_mime_and_host_filters_still_apply(self):
        mutations=[lambda x:x['extmetadata']['LicenseShortName'].update(value='All rights reserved'),lambda x:x.update(width=499),lambda x:x.update(mime='image/svg+xml'),lambda x:x.update(url='https://evil.invalid/image.jpg'),lambda x:x.update(url='http://upload.wikimedia.org/test.jpg'),lambda x:x.update(descriptionurl='https://evil.invalid/page')]
        for mutation in mutations:
            payload=self.payload();mutation(payload['query']['pages']['1']['imageinfo'][0])
            with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertEqual(len(m.resolve_titles(['File:One.jpg','File:Two.png'],'event','korean')),1)

    def test_invalid_nested_or_injected_titles_never_fetch(self):
        for titles in [None,[],['File:One.jpg'],['File:One.jpg']*2,['File:One.jpg|File:Injected.jpg','File:Two.png'],['File:One.jpg\n','File:Two.png'],['https://evil.invalid','File:Two.png'],[{'title':'private'},'File:Two.png']]:
            with patch.object(m.urllib.request,'urlopen') as network:
                with self.assertRaises(ValueError):m.resolve_titles(titles,'event','korean')
                network.assert_not_called()

    def test_direct_route_has_no_search_fallback_and_stops_below_two(self):
        job={'topic_key':'event','channel':'korean','visual_source_titles':['File:One.jpg','File:Two.png']}
        with patch.object(m,'resolve_titles',return_value={}),patch.object(m,'search') as search:
            with self.assertRaisesRegex(RuntimeError,'Not enough'):m.candidates([job])
            search.assert_not_called()

    def test_stage_accepts_only_two_plain_title_strings_and_does_not_stamp(self):
        jobs=valid_jobs();jobs[0]['visual_source_titles']=['File:One.jpg','File:Two.png'];before=copy.deepcopy(jobs)
        s.validate_staged(jobs,DAY,{'publications':[]})
        self.assertEqual(jobs,before)
        jobs[0]['visual_source_titles'][0]={'private':'CANARY'}
        with self.assertRaises(ValueError):s.validate_staged(jobs,DAY,{'publications':[]})

    def test_unapproved_thumbnail_falls_back_only_to_approved_original(self):
        payload=self.payload()
        for page in payload['query']['pages'].values():page['imageinfo'][0]['thumburl']='https://thumb.wikimedia.org/unapproved.jpg'
        with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            result=m.resolve_titles(['File:One.jpg','File:Two.png'],'event','korean')
        self.assertEqual(len(result),2)
        self.assertTrue(all(v['url']=='https://upload.wikimedia.org/test.jpg' for v in result.values()))
        for page in payload['query']['pages'].values():page['imageinfo'][0]['url']='https://evil.invalid/original.jpg'
        with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            self.assertEqual(m.resolve_titles(['File:One.jpg','File:Two.png'],'event','korean'),{})
