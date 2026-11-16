import copy,json,os,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from test_world_kitchen import episode
from world_kitchen.automatic import run,align_scenes,source_bound

class Journal:
    def __init__(self):self.data={'days':{}};self.saves=[]
    def save(self):self.saves.append(copy.deepcopy(self.data))
class Provider:
    logo='wordmark.png'
    music='music.mp3';music_rights_source='https://example.com/music-license';music_credit='Mock music'
    def __init__(self):self.calls=[];self.reject=None;self.fail=None
    def hit(self,name):
        self.calls.append(name)
        if self.fail==name:raise RuntimeError('uncertain')
    def preflight(self,**kw):self.hit('preflight')
    def research(self,day,recent):self.hit('research');return episode(),[{'url':'https://example.com/recipe'}]
    def review(self,e,folder):
        stage='final_review' if folder else 'recipe_review';self.hit(stage);return {'approved':self.reject!=stage}
    def image(self,*a):self.hit('image')
    def tts(self,text,root):
        self.hit('tts');p=root/'voice.mp3';p.write_bytes(b'audio')
        n=len(text);return p,dict(characters=list(text),character_start_times_seconds=[i*12/n for i in range(n)],character_end_times_seconds=[(i+1)*12/n for i in range(n)]),12
    def transcribe(self,*a):self.hit('transcribe');return 'verified transcript'
    def check_inventory(self,*a):self.hit('inventory')
    def upload(self,*a):self.hit('upload');return 'abcdefghijk'
    def captions(self,*a):self.hit('captions');return 'caption-id'
    def schedule(self,*a):self.hit('schedule');return True
    def verify(self,*a):self.hit('verify');return True

def render(e,out):
    out.mkdir();(out/'video.mp4').write_bytes(b'fixture');(out/'english.srt').write_text('english');return out/'video.mp4'
CLOCK=lambda:datetime(2026,11,17,5,tzinfo=timezone.utc)
class CookingAutomatic(unittest.TestCase):
    def execute(self,publish=True,provider=None,journal=None):
        p=provider or Provider();j=journal or Journal()
        with tempfile.TemporaryDirectory() as d:
            result=run('2026-11-17',p,j,Path(d)/'episode',publish=publish,clock=CLOCK,renderer=render)
        return result,p,j
    def test_full_mocked_path(self):
        r,p,j=self.execute();self.assertEqual(r['status'],'scheduled_verified')
        self.assertLess(p.calls.index('captions'),p.calls.index('schedule'))
        self.assertLess(p.calls.index('final_review'),p.calls.index('upload'))
        self.assertTrue(any(v['days']['2026-11-17']['status']=='private_upload_submitted' for v in j.saves))
    def test_render_only_never_uploads(self):
        r,p,j=self.execute(False);self.assertEqual(r['status'],'rendered_reviewed_not_uploaded');self.assertNotIn('upload',p.calls)
    def test_preflight_blocks_generation(self):
        p=Provider();p.fail='preflight';j=Journal()
        with self.assertRaises(RuntimeError):self.execute(provider=p,journal=j)
        self.assertEqual(j.saves,[]);self.assertNotIn('research',p.calls)
    def test_rejected_recipe_never_generates(self):
        p=Provider();p.reject='recipe_review';r,p,j=self.execute(provider=p)
        self.assertEqual(r['status'],'recipe_rejected');self.assertNotIn('image',p.calls)
    def test_rejected_final_never_uploads(self):
        p=Provider();p.reject='final_review';r,p,j=self.execute(provider=p)
        self.assertEqual(r['status'],'final_review_rejected');self.assertNotIn('upload',p.calls)
    def test_uncertain_upload_never_repeated(self):
        p=Provider();p.fail='upload';j=Journal()
        with self.assertRaises(RuntimeError):self.execute(provider=p,journal=j)
        self.assertEqual(j.data['days']['2026-11-17']['status'],'private_upload_submitted')
        r,p,j=self.execute(provider=p,journal=j)
        self.assertEqual(r['status'],'already_attempted');self.assertEqual(p.calls.count('upload'),1)
    def test_caption_fail_keeps_video_receipt_private(self):
        p=Provider();p.fail='captions';j=Journal()
        with self.assertRaises(RuntimeError):self.execute(provider=p,journal=j)
        self.assertEqual(j.data['days']['2026-11-17']['video_id'],'abcdefghijk');self.assertNotIn('schedule',p.calls)
    def test_near_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider()
            with self.assertRaises(ValueError):run('2026-11-17',p,Journal(),d,clock=lambda:datetime(2026,11,17,9,59,tzinfo=timezone.utc))
            self.assertEqual(p.calls,[])
    def test_unobserved_source(self):
        with self.assertRaises(ValueError):source_bound(episode(),[])
    def test_image_cap(self):
        e=episode();e['scenes']*=3
        with self.assertRaises(ValueError):source_bound(e,[{'url':e['sources'][0]}])
    def test_alignment_mismatch(self):
        with self.assertRaises(ValueError):align_scenes(episode(),{'characters':['x']},12)
    def test_production_branch_restriction(self):
        from world_kitchen.providers import Providers
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/other'}):
            with self.assertRaisesRegex(RuntimeError,'requires main'):Providers().preflight()
    def test_missing_configuration_no_network(self):
        from world_kitchen.providers import Providers
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'Missing cooking configuration'):Providers().preflight(publish=True)
    def test_included_credits_not_overage(self):
        from world_kitchen.providers import Providers
        with patch('pipeline.subscription',return_value={'character_count':100,'character_limit':101}):
            with self.assertRaisesRegex(RuntimeError,'Insufficient included'):Providers().credit_check(100)
    def test_wrong_stored_channel_no_refresh(self):
        from world_kitchen.providers import Providers
        with patch.dict(os.environ,{'YOUTUBE_KITCHEN_OAUTH':json.dumps({'channel_id':'wrong'})}):
            with self.assertRaisesRegex(ValueError,'Wrong stored'):Providers().auth()
    def test_only_private_owned_video_schedule(self):
        from world_kitchen.providers import Providers
        p=Providers()
        with patch.object(p,'videos',return_value=[dict(snippet={'channelId':'wrong'},status={'privacyStatus':'private'})]):
            with self.assertRaises(ValueError):p.schedule('abcdefghijk','2026-11-17T19:00:00+09:00','caption-id')
    def test_no_schedule_before_caption_serving(self):
        from world_kitchen.providers import Providers,CHANNEL
        p=Providers();p.poll_attempts=1
        with patch.object(p,'videos',return_value=[dict(snippet={'channelId':CHANNEL},status={'privacyStatus':'private'},processingDetails={'processingStatus':'succeeded'})]), patch.object(p,'caption_ready',return_value=False), patch('world_kitchen.google_transport.http') as write:
            with self.assertRaisesRegex(RuntimeError,'Exact English caption not serving'):p.schedule('abcdefghijk','2026-11-17T19:00:00+09:00','caption-id')
            write.assert_not_called()
    def test_no_schedule_before_processing(self):
        from world_kitchen.providers import Providers,CHANNEL
        p=Providers();p.poll_attempts=1
        with patch.object(p,'videos',return_value=[dict(snippet={'channelId':CHANNEL},status={'privacyStatus':'private'},processingDetails={'processingStatus':'processing'})]):
            with self.assertRaisesRegex(RuntimeError,'Processing incomplete'):p.schedule('abcdefghijk','2026-11-17T19:00:00+09:00','caption-id')

if __name__=='__main__':unittest.main()
