import copy
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

    def test_preflight_does_not_require_retired_secret_voice_or_identity(self):
        cfg=c.p.cfg();voice=cfg['channels']['ojirap']['voice_id']
        env={k:'test' for k in ('OPENAI_API_KEY','ELEVENLABS_API_KEY','YOUTUBE_OJIRAP_OAUTH','GITHUB_TOKEN','GITHUB_REPOSITORY')}
        with patch.dict(c.os.environ,env,clear=True),patch.object(c,'http',return_value={'data':[{'id':'gpt-5.4-mini'}]}),patch.object(c.p,'list_voices',return_value=[{'voice_id':voice}]),patch.object(c,'youtube_token',return_value='token') as token,patch.object(c,'verify_channel') as verify,patch.object(c.p,'subscription',return_value={'character_limit':6000,'character_count':0}),patch.object(c.media_renderer,'font'),patch.object(c.p,'ffmpeg'):
            c.preflight()
        token.assert_called_once_with('ojirap');verify.assert_called_once_with('ojirap','token')
