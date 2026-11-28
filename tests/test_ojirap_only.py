import copy
from datetime import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cloud_runner as c
import media_renderer
import additional_runtime as ar
from test_guards import batch
from test_source_manifest import manifest_for

DAY='2026-11-05'
def active(): return [j for j in batch() if j['channel']=='ojirap']

class OjirapOnly(unittest.TestCase):
    def test_two_jobs_bind_and_validate_without_losing_fact_guards(self):
        jobs=active();bound=c.bind_researched_sources(jobs,manifest_for(jobs))
        c.validate_batch(bound,DAY,set())
        changed=copy.deepcopy(bound);changed[0]['fact_packet_hash']='tampered'
        with self.assertRaisesRegex(ValueError,'packet mismatch'):c.validate_batch(changed,DAY,set())
        self.assertEqual(len(bound),2)

    def test_retired_work_is_rejected_before_paid_or_network_effects(self):
        jobs=batch()
        with patch.object(c,'openai') as api,patch.object(c.p,'api') as tts,patch.object(c,'review_candidates') as media:
            with self.assertRaisesRegex(ValueError,'disabled'):c.correct_narration_lengths_once(jobs)
            with self.assertRaisesRegex(ValueError,'disabled'):c.review_jobs({'days':{DAY:{}}},'sha',DAY,jobs)
            with self.assertRaisesRegex(ValueError,'disabled'):c.p.synthesize(jobs[0],Path('/unused'))
            with patch.object(c.p,'read',return_value=jobs[0]):
                with self.assertRaisesRegex(ValueError,'disabled'):media_renderer.render(Path('/unused'))
                with self.assertRaisesRegex(ValueError,'disabled'):c.p.render(Path('/unused'),smoke=True)
            with self.assertRaisesRegex(ValueError,'disabled'):ar.Runtime('sha').review_request({'job':jobs[0]},'model',100,1)
            api.assert_not_called();tts.assert_not_called();media.assert_not_called()

    def test_unexpected_english_draft_holds_before_correction(self):
        ledger={'days':{DAY:{}}}
        with patch.object(c,'journal_put'),patch.object(c,'correct_narration_lengths_once') as fix:
            with self.assertRaisesRegex(RuntimeError,'active_channel_scope'):
                c.guarded_draft_validation(ledger,'sha',DAY,json.dumps({'jobs':batch()}),set(),active_only=True,allow_length_correction=True)
            fix.assert_not_called()
        self.assertTrue(ledger['days'][DAY]['retry_hold'])

    def test_two_ojirap_jobs_publish_without_english_render(self):
        jobs=active();media={}
        for j in jobs:
            for i,scene in enumerate(j['scenes']):
                key=j['id']+str(i);scene['media']=key
                media[key]={'topic_key':j['topic_key'],'channel':j['channel'],'relevance_verified':True}
        ledger={'days':{DAY:{'jobs':jobs,'media':media}},'publications':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(c.p,'ROOT',Path(tmp)),patch.object(c,'journal_put',return_value='sha'),patch.object(c,'download_media'),patch.object(c.daily_graphics,'ensure'),patch.object(c.media_renderer,'render') as render,patch.object(c,'youtube_token',return_value='test') as token,patch.object(c,'verify_channel'),patch.object(c,'upload',side_effect=['v1','v2']) as upload:
            c.publish_reviewed(ledger,'sha',DAY)
        self.assertEqual(render.call_count,2);self.assertEqual(upload.call_count,2)
        self.assertEqual([x.args[0] for x in token.call_args_list],['ojirap','ojirap'])
        self.assertEqual([j['channel'] for j in ledger['publications']],['ojirap','ojirap'])

    def test_graphics_only_is_logged_and_not_rendered_or_uploaded(self):
        for blocked in (1, 2):
            with self.subTest(blocked=blocked):
                jobs=active()
                media={}
                for ix,job in enumerate(jobs):
                    for i,scene in enumerate(job['scenes']):
                        key=('graphic-' if ix<blocked else 'photo-')+job['id']+str(i)
                        scene['media']=key
                        media[key]={'topic_key':job['topic_key'],'channel':'ojirap','relevance_verified':True}
                ledger={'days':{DAY:{'jobs':jobs,'media':media}},'publications':[]}
                with tempfile.TemporaryDirectory() as tmp, patch.object(c.p,'ROOT',Path(tmp)), patch.object(c,'journal_put',return_value='sha'), patch.object(c,'download_media') as download, patch.object(c.daily_graphics,'ensure'), patch.object(c.media_renderer,'render') as render, patch.object(c,'youtube_token',return_value='test'), patch.object(c,'verify_channel'), patch.object(c,'upload',return_value='v') as upload:
                    c.publish_reviewed(ledger,'sha',DAY)
                self.assertEqual(render.call_count,2-blocked)
                self.assertEqual(upload.call_count,2-blocked)
                self.assertEqual(download.call_count,int(blocked==1))
                self.assertEqual(ledger['days'][DAY]['status'],'complete')
                self.assertEqual([x['reason'] for x in ledger['days'][DAY]['publication_exclusions']],['graphics_only_not_publishable']*blocked)

    def test_regular_research_uses_photo_contract_despite_graphics_config(self):
        with patch.object(c.daily_graphics, 'enabled', return_value=True):
            instructions = c.research_instructions(photo_only=True)
            self.assertIn('two distinct relevant real', instructions)
            self.assertIn('replace the story during this same search', instructions)
            self.assertNotIn(c.GRAPHICS_RESEARCH_NOTE, instructions)
            self.assertIn(c.GRAPHICS_RESEARCH_NOTE, c.research_instructions())

    def test_overnight_does_not_start_new_day_or_write_journal(self):
        for hour, minute, should_start in [(0,38,False),(4,59,False),(5,0,True)]:
            now=datetime(2026,11,28,hour,minute,tzinfo=c.p.KST)
            ledger={'days':{},'publications':[]}
            with self.subTest(now=now), patch.object(c.p,'now',return_value=now), patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}), patch.object(c,'journal_get',return_value=(ledger,'sha')), patch.object(c,'journal_put') as save, patch.object(c,'preflight',side_effect=ValueError('start reached')) as preflight, patch.object(c,'openai') as paid, patch.object(c,'publish_reviewed') as publish:
                if should_start:
                    with self.assertRaisesRegex(ValueError,'start reached'): c.produce()
                else:
                    c.produce()
                self.assertEqual(preflight.call_count,int(should_start))
                save.assert_not_called();paid.assert_not_called();publish.assert_not_called()

    def test_overnight_interrupted_submission_still_requires_reconciliation(self):
        now=datetime(2026,11,28,0,38,tzinfo=c.p.KST)
        ledger={'days':{'2026-11-28':{'status':'upload_submitted'}},'publications':[]}
        with patch.object(c.p,'now',return_value=now), patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}), patch.object(c,'journal_get',return_value=(ledger,'sha')), patch.object(c,'preflight') as preflight:
            with self.assertRaisesRegex(RuntimeError,'reconcile'):c.produce()
            preflight.assert_not_called()

    def test_photo_only_is_not_reclassified_to_graphics_and_no_photo_skips_review(self):
        jobs=c.bind_researched_sources(active(),manifest_for(active()))
        photo_ids=['first','second']
        media={k:{'topic_key':jobs[0]['topic_key'],'channel':'ojirap'} for k in photo_ids}
        failed={'status':'FAIL','issues':[{'code':'media_relevance','job_index':0,'scene_index':-1}],'selection':{}}
        ledger={'days':{DAY:{}},'publications':[]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.p,'ROOT',Path(tmp)), patch.object(c,'journal_put',return_value='sha'), patch.object(c,'review_candidates',return_value=media), patch.object(c.daily_graphics,'enabled',return_value=True), patch.object(c.daily_graphics,'build') as cards, patch.object(c,'openai',return_value=(json.dumps(failed),[])) as paid:
            with self.assertRaisesRegex(RuntimeError,'Editorial review did not pass'):
                c.review_jobs(ledger,'sha',DAY,jobs,photo_only=True)
            paid.assert_called_once()
            cards.assert_not_called()
        rec=ledger['days'][DAY]
        self.assertEqual(rec['status'],'editorial_review_failed')
        self.assertEqual([x['code'] for x in rec['editorial_diagnostic']['issues']],['media_relevance','insufficient_media'])

    def test_all_photo_insufficiency_completes_without_paid_review_or_next_slot(self):
        jobs=c.bind_researched_sources(active(),manifest_for(active()))
        ledger={'days':{DAY:{}},'publications':[]}
        now=datetime.fromisoformat(DAY+'T08:00:00+09:00')
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.p,'ROOT',Path(tmp)), patch.object(c.p,'now',return_value=now), patch.object(c,'journal_put',return_value='new') as put, patch.object(c,'review_candidates',return_value={}), patch.object(c,'auto_exclude_enabled',return_value=True), patch.object(c,'openai') as paid, patch.object(c,'publish_reviewed') as publish, patch.object(c,'journal_get',return_value=(ledger,'new')), patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}):
            self.assertEqual(c.review_jobs(ledger,'sha',DAY,jobs,photo_only=True),'new')
            put.assert_called_once()
            c.produce()
            paid.assert_not_called()
            publish.assert_not_called()
        rec=ledger['days'][DAY]
        self.assertEqual(rec['status'],'complete')
        self.assertEqual(rec['completion_reason'],'no_photo_topics')
        self.assertFalse(rec['retry_hold'])
        self.assertEqual([x['codes'] for x in rec['editorial_exclusions']],[['insufficient_media']]*2)
        self.assertEqual(ledger['publications'],[])

    def test_production_considers_both_photo_queries_with_same_review_cap(self):
        job=active()[0]
        job['visual_queries']=['subject','relevant alternative']
        with patch.object(c.relevant_media,'search',side_effect=[dict.fromkeys(['a','b','c','d','e']),dict.fromkeys(['x','y','z'])]) as search:
            result=c.relevant_media.candidates([job],diversify=True)
        self.assertEqual(list(result),['a','x','b','y'])
        self.assertEqual([x.args[0] for x in search.call_args_list],job['visual_queries'])

    def test_sparse_visual_search_uses_labeled_related_category(self):
        job=active()[0]; job['visual_queries']=['ROKS Sohn Won-yil', 'Korean Type 214 submarines']
        photo={'media_type':'image','notice':'자료사진 · 2008','license':'Public domain'}
        with patch.object(c.relevant_media,'search',side_effect=[{'a':photo},{'a':photo}]), patch.object(c.relevant_media,'search_related_category',return_value={'a':photo,'b':photo,'c':photo}) as category:
            result=c.relevant_media.candidates([job],diversify=True)
        category.assert_called_once_with(job['visual_queries'][1],job['topic_key'],'ojirap')
        self.assertEqual(list(result),['a','b','c'])
        self.assertNotIn('illustrative_only',result['a'])
        self.assertEqual(result['b']['notice'],'자료화면 · 실제 현장 아님')
        self.assertTrue(result['b']['illustrative_only'])
        self.assertEqual(result['b']['license'],photo['license'])

    def test_related_category_cannot_fill_a_story_without_two_real_assets(self):
        job=active()[0]; job['visual_queries']=['direct subject','related subject']
        with patch.object(c.relevant_media,'search',return_value={}), patch.object(c.relevant_media,'search_related_category',return_value={'only':{'media_type':'image'}}):
            with self.assertRaises(c.relevant_media.InsufficientCandidates):
                c.relevant_media.candidates([job],diversify=True)

    def test_direct_upload_stops_graphics_only_before_network(self):
        job=active()[0]
        for scene in job['scenes']:
            scene['media']='graphic-fallback'
        with patch.object(c.urllib.request,'urlopen') as network:
            with self.assertRaisesRegex(RuntimeError,'graphics-only'):
                c.upload(Path('/unused'),job,'test')
        network.assert_not_called()

    def test_preflight_does_not_require_retired_secret_voice_or_identity(self):
        cfg=c.p.cfg();voice=cfg['channels']['ojirap']['voice_id']
        env={k:'test' for k in ('OPENAI_API_KEY','ELEVENLABS_API_KEY','YOUTUBE_OJIRAP_OAUTH','GITHUB_TOKEN','GITHUB_REPOSITORY')}
        with patch.dict(c.os.environ,env,clear=True),patch.object(c,'http',return_value={'data':[{'id':'gpt-5.4-mini'}]}),patch.object(c.p,'list_voices',return_value=[{'voice_id':voice}]),patch.object(c,'youtube_token',return_value='token') as token,patch.object(c,'verify_channel') as verify,patch.object(c.p,'subscription',return_value={'character_limit':6000,'character_count':0}),patch.object(c.media_renderer,'font'),patch.object(c.p,'ffmpeg'):
            c.preflight()
        token.assert_called_once_with('ojirap');verify.assert_called_once_with('ojirap','token')
