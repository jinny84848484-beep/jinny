import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import operator_review_recovery_20261117 as r

class OperatorReviewGuards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ledger={'days':{r.DAY:{'status':'draft_submitted','checkpoints':{'draft':{'status':'submitted'}}}},'publications':[]}
        self.jobs=[{'id':r.DAY+'-test-'+str(n),'channel':'ojirap','topic_key':'topic-'+str(n),'scenes':[{'kind':'fact','headline':'Test','body':'Body','narration':'Text'}], 'sources':[]} for n in range(2)]
        media={}
        for job in self.jobs:
            key=r.g.media_key(job,0);job['scenes'][0]['media']=key
            rel=r.g.relative_path(job,0);path=self.root/rel;path.parent.mkdir(parents=True);path.write_bytes(b'PNG fixture')
            media[key]={'path':rel,'policy':r.g.POLICY,'scene_index':0,'channel':'ojirap','topic_key':job['topic_key'],
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'text_hash':hashlib.sha256(json.dumps(r.g.card_text(job,0),ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
        self.data={'day':r.DAY,'creator':'writer','jobs':self.jobs,'media':media,'review':{'reviewer':'independent','status':'PASS_PRE_RENDER_ONLY','issues':[],'final_video_audio_qa':'PENDING','publication_authorization':False,
            'pre_render_checks':dict.fromkeys(r.CHECKS,True),'checked_at':r.c.p.now().isoformat(),
            'jobs_sha256':r.c.fingerprint(self.jobs),'media_sha256':r.c.fingerprint(media)}}
        self.record_patch=patch.object(r,'RECORD_HASH',r.c.fingerprint(self.ledger['days'][r.DAY]));self.record_patch.start()
    def tearDown(self):self.record_patch.stop();self.tmp.cleanup()
    def verify(self,data=None,ledger=None,pin=True):
        raw=json.dumps(data or self.data).encode()
        with patch.object(r,'APPROVED_PACKAGE_SHA256',hashlib.sha256(raw).hexdigest() if pin else None),patch.object(r.c,'validate_batch') as validate:
            result=r.verify_package(raw,ledger or self.ledger,self.root)
            validate.assert_called_once();return result
    def test_valid_does_not_mutate_ledger_or_jobs(self):
        before=copy.deepcopy((self.ledger,self.data));self.verify();self.assertEqual(before,(self.ledger,self.data))
    def test_unpinned_and_live_rejected(self):
        with self.assertRaises(ValueError):self.verify(pin=False)
        with patch.object(r,'LIVE_ENABLED',False),patch.object(r.c,'http') as http,patch.object(r.c,'journal_put') as write:
            with self.assertRaises(RuntimeError):r.run()
            http.assert_not_called();write.assert_not_called()
    def test_failed_self_or_missing_check_rejected(self):
        for change in ({'status':'FAIL'},{'reviewer':'writer'},{'pre_render_checks':{}},{'issues':['unsafe']}):
            d=copy.deepcopy(self.data);d['review'].update(change)
            with self.assertRaises(ValueError):self.verify(d)
    def test_changed_job_png_or_text_hash_rejected(self):
        d=copy.deepcopy(self.data);d['jobs'][0]['scenes'][0]['narration']='changed'
        with self.assertRaises(ValueError):self.verify(d)
        d=copy.deepcopy(self.data);d['jobs'][0]['scenes'][0]['body']='changed';d['review']['jobs_sha256']=r.c.fingerprint(d['jobs'])
        with self.assertRaises(ValueError):self.verify(d)
        path=self.root/next(iter(d['media'].values()))['path'];path.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.verify()
    def test_record_reentry_and_publication_rejected(self):
        ledger=copy.deepcopy(self.ledger);ledger['days'][r.DAY]['status']='reviewed'
        with self.assertRaises(ValueError):self.verify(ledger=ledger)
        ledger=copy.deepcopy(self.ledger);ledger['publications']=[{'job_id':r.DAY+'-other'}]
        with self.assertRaises(ValueError):self.verify(ledger=ledger)

    def test_both_cas_conflicts_stop_before_paid_or_upload(self):
        path=self.root/'package.json';path.write_text('{}')
        for writes in ([RuntimeError('CAS conflict')],['next-sha',RuntimeError('CAS conflict')]):
            ledger=copy.deepcopy(self.ledger)
            with patch.object(r,'LIVE_ENABLED',True),patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'jinny84848484-beep/jinny','PUBLIC_UPLOADS_ENABLED':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1','RUN_MODE':'operator_preview_20261117'}), \
                 patch.object(r,'verify_package',return_value=copy.deepcopy(self.data)),patch.object(r,'preflight_without_openai'), \
                 patch.object(r.c,'journal_get',return_value=(ledger,'first-sha')),patch.object(r.c,'journal_put',side_effect=writes) as put, \
                 patch.object(r.c.p,'write'),patch.object(r.c.relevant_media,'apply_review'),patch.object(r.c,'publish_reviewed') as publish,patch.object(r.c,'openai') as openai:
                with self.assertRaisesRegex(RuntimeError,'CAS conflict'):r.run(path)
                publish.assert_not_called();openai.assert_not_called()
                self.assertEqual(put.call_count,len(writes))
                rec=ledger['days'][r.DAY]
                self.assertEqual(rec['operator_review_recovery']['previous_record'],self.ledger['days'][r.DAY])
                self.assertEqual(rec['checkpoints'],self.ledger['days'][r.DAY]['checkpoints'])

    def test_preflight_has_no_openai_calls(self):
        with patch.dict(r.c.os.environ,dict.fromkeys(['ELEVENLABS_API_KEY','YOUTUBE_OJIRAP_OAUTH','GITHUB_TOKEN','GITHUB_REPOSITORY'],'test')), \
             patch.object(r.c.p,'list_voices',return_value=[{'voice_id':r.c.p.cfg()['channels']['ojirap']['voice_id']}]), \
             patch.object(r.c,'youtube_token',return_value='test'),patch.object(r.c,'verify_channel'), \
             patch.object(r.c.p,'subscription',return_value={'character_limit':2000,'character_count':0}), \
             patch.object(r.c.media_renderer,'font'),patch.object(r.c.p,'ffmpeg'),patch.object(r.c,'http') as http,patch.object(r.c,'openai') as openai:
            r.preflight_without_openai();http.assert_not_called();openai.assert_not_called()

    def test_render_route_stops_at_final_qa_without_upload(self):
        path=self.root/'package.json';path.write_text('{}');ledger=copy.deepcopy(self.ledger)
        for job in self.data['jobs']:job['sources']=[]
        with patch.object(r,'LIVE_ENABLED',True),patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'jinny84848484-beep/jinny','PUBLIC_UPLOADS_ENABLED':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1','RUN_MODE':'operator_preview_20261117'}), \
             patch.object(r,'verify_package',return_value=copy.deepcopy(self.data)),patch.object(r,'preflight_without_openai'), \
             patch.object(r.c,'journal_get',return_value=(ledger,'sha')),patch.object(r.c,'journal_put',return_value='next'), \
             patch.object(r.c.p,'write'),patch.object(r.c.relevant_media,'apply_review'),patch.object(r.c,'validate_batch'),patch.object(r.c,'assert_additional_day_budget'), \
             patch.object(r.c.daily_graphics,'ensure'),patch.object(r.c,'download_media'),patch.object(r.c.p,'validate'), \
             patch.object(r.c.media_renderer,'render') as render,patch.object(r.c,'upload') as upload,patch.object(r.c,'openai') as openai:
            result=r.run(path)
            self.assertEqual(result,{'status':'operator_final_qa_pending','uploads':0})
            self.assertEqual(render.call_count,2);upload.assert_not_called();openai.assert_not_called()
            self.assertEqual(ledger['days'][r.DAY]['status'],'operator_final_qa_pending')
            self.assertEqual(ledger['days'][r.DAY]['checkpoints'],self.ledger['days'][r.DAY]['checkpoints'])

    def test_real_billing_guard_rejects_quota_and_input_changes(self):
        path=self.root/'package.json';path.write_text('{}')
        for failure in ('quota','speech','config'):
            ledger=copy.deepcopy(self.ledger)
            def render(job_path,*,billing_guard):
                job=self.prepared
                speech='['+r.c.p.cfg()['channels']['ojirap']['delivery']+'] '+' '.join(x['narration'] for x in job['scenes'])
                config=copy.deepcopy(r.c.p.cfg())
                if failure=='speech':speech+=' changed'
                if failure=='config':config['model_id']='changed'
                billing_guard(job,speech,config)
                self.fail('Guard must reject before synthesis')
            def write(path,value):
                if '/jobs/' in str(path):self.prepared=copy.deepcopy(value)
            with patch.object(r,'LIVE_ENABLED',True),patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'jinny84848484-beep/jinny','PUBLIC_UPLOADS_ENABLED':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1','RUN_MODE':'operator_preview_20261117'}), \
                 patch.object(r,'verify_package',return_value=copy.deepcopy(self.data)),patch.object(r,'preflight_without_openai'), \
                 patch.object(r.c,'journal_get',return_value=(ledger,'sha')),patch.object(r.c,'journal_put',return_value='next'), \
                 patch.object(r.c.p,'write',side_effect=write),patch.object(r.c.relevant_media,'apply_review'),patch.object(r.c,'validate_batch'),patch.object(r.c,'assert_additional_day_budget'), \
                 patch.object(r.c.daily_graphics,'ensure'),patch.object(r.c,'download_media'),patch.object(r.c.p,'validate'), \
                 patch.object(r.c.p,'subscription',return_value={'character_limit':0,'character_count':0}),patch.object(r.c.media_renderer,'render',side_effect=render),patch.object(r.c,'upload') as upload:
                with self.assertRaises((ValueError,RuntimeError)):r.run(path)
                upload.assert_not_called()
