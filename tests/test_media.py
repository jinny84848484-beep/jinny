import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import relevant_media as m
import media_renderer as r

class MediaGuards(unittest.TestCase):
    def test_unknown_license_never_becomes_a_candidate(self):
        payload={'query':{'pages':{'1':{'pageid':1,'title':'File:Person.jpg','imageinfo':[{'mime':'image/jpeg','width':1000,'height':1000,'url':'https://upload.wikimedia.org/test.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Person.jpg','extmetadata':{'LicenseShortName':{'value':'All rights reserved'}}}]}}}}
        with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            self.assertEqual(m.search('specific person','event','ojirap'),{})

    def test_cross_story_media_rejected_without_writing_library(self):
        job={'id':'job','topic_key':'one','scenes':[{},{}]}
        media={'a':{'topic_key':'two'},'b':{'topic_key':'one'}}
        with patch.object(m.p,'write') as write:
            with self.assertRaisesRegex(ValueError,'another story'):m.apply_review([job],media,{'job':['a','b']})
            write.assert_not_called()

    def test_one_repeated_photo_does_not_pass(self):
        with self.assertRaisesRegex(ValueError,'two reviewed'):
            m.apply_review([{'id':'job','scenes':[{},{}]}],{'a':{}},{'job':['a','a']})

    def test_unrelated_blank_visual_cannot_render(self):
        with self.assertRaisesRegex(ValueError,'Directly relevant'):
            r.overlay({},r.p.cfg()['channels']['ojirap'],0,4,None,Path('never-written.png'))

    def test_caption_timing_preserves_all_spoken_words(self):
        spoken='근데 이게 무슨 일이야? 진짜 이상하잖아.'
        alignment={'characters':list(spoken),'character_start_times_seconds':[i*.1 for i in range(len(spoken))],'character_end_times_seconds':[(i+1)*.1 for i in range(len(spoken))]}
        cues=r.captions(spoken,alignment,True)
        self.assertEqual(' '.join(x[2] for x in cues),spoken)
        self.assertTrue(all(a<b for a,b,_ in cues))

if __name__=='__main__':unittest.main()
