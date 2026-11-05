import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import cloud_runner as c
from test_guards import batch
from test_source_manifest import manifest_for,evidence_for


class StageCheckpoints(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root=patch.object(c.p,'ROOT',Path(self.directory.name))
        root.start();self.addCleanup(root.stop)
        self.ledger={'days':{'2026-11-05':{}},'publications':[{'video_id':'old'}]}
        self.writes=[]
        def save(value,sha):
            self.writes.append(copy.deepcopy(value))
            return 'sha-'+str(len(self.writes))
        journal=patch.object(c,'journal_put',side_effect=save)
        journal.start();self.addCleanup(journal.stop)

    def checkpoint(self,submit,request=None):
        return c.stage_checkpoint(self.ledger,'sha','2026-11-05','research',request=request or {'prompt':'source-backed private draft'},submit=submit)

    def test_submission_is_durable_before_call_and_completed_result_reused(self):
        def provider():
            self.assertEqual(self.writes[-1]['days']['2026-11-05']['checkpoints']['research']['status'],'submitted')
            return 'private research',[{'url':'https://example.org/report'}]
        provider=Mock(side_effect=provider)
        first,_=self.checkpoint(provider)
        second,_=self.checkpoint(provider)
        self.assertEqual(first,second);provider.assert_called_once()
        self.assertEqual(len(self.writes),2)
        self.assertEqual(self.ledger['publications'],[{'video_id':'old'}])
        public=json.dumps(self.ledger)
        self.assertNotIn('private research',public)
        self.assertNotIn('source-backed private draft',public)
        self.assertNotIn('example.org',public)

    def test_pending_never_repeats_even_with_result_file_present(self):
        self.ledger['days']['2026-11-05']['checkpoints']={'research':{'status':'submitted'}}
        provider=Mock()
        with self.assertRaisesRegex(RuntimeError,'reconcile'):self.checkpoint(provider)
        provider.assert_not_called()

    def test_missing_or_tampered_checkpoint_never_repeats(self):
        for tamper in (False,True):
            with self.subTest(tamper=tamper):
                self.ledger['days']['2026-11-05']={}
                self.checkpoint(Mock(return_value=('private',[])))
                path=c.p.ROOT/'runs/2026-11-05/checkpoints/research.json'
                if tamper:path.write_text('{}',encoding='utf-8')
                else:path.unlink()
                provider=Mock()
                with self.assertRaisesRegex(RuntimeError,'unavailable|hash mismatch'):self.checkpoint(provider)
                provider.assert_not_called()

    def test_changed_input_never_reuses_or_regenerates(self):
        self.checkpoint(Mock(return_value=('original',[])))
        provider=Mock()
        with self.assertRaisesRegex(RuntimeError,'input changed'):self.checkpoint(provider,{'prompt':'different'})
        provider.assert_not_called()

    def test_uncertain_network_result_remains_submitted(self):
        provider=Mock(side_effect=RuntimeError('network result uncertain'))
        with self.assertRaisesRegex(RuntimeError,'uncertain'):self.checkpoint(provider)
        self.assertEqual(self.writes[-1]['days']['2026-11-05']['checkpoints']['research']['status'],'submitted')
        with self.assertRaisesRegex(RuntimeError,'reconcile'):self.checkpoint(provider)
        provider.assert_called_once()

    def test_submission_write_failure_never_calls_provider(self):
        provider=Mock()
        with patch.object(c,'journal_put',side_effect=RuntimeError('write uncertain')):
            with self.assertRaisesRegex(RuntimeError,'write uncertain'):self.checkpoint(provider)
        provider.assert_not_called()

    def test_completed_unusable_is_artifacted_and_cannot_be_reused(self):
        failure=c.CompletedResearchUnusable('no_recognized_source_evidence',{'output':[]},[],'private raw result')
        provider=Mock(side_effect=failure)
        with self.assertRaises(c.CompletedResearchUnusable):self.checkpoint(provider)
        record=self.ledger['days']['2026-11-05']
        self.assertTrue(record['retry_hold'])
        self.assertFalse(record['checkpoints']['research']['usable'])
        self.assertNotIn('private raw result',json.dumps(self.ledger))
        self.assertIn('private raw result',(c.p.ROOT/'runs/2026-11-05/checkpoints/research.json').read_text())
        with self.assertRaisesRegex(RuntimeError,'unusable'):self.checkpoint(provider)
        provider.assert_called_once()


class EditorialDecision(unittest.TestCase):
    def setUp(self):
        self.jobs=batch()
        self.media={};self.selection={}
        for index,job in enumerate(self.jobs):
            ids=[f'image-{index}-a',f'image-{index}-b']
            for key in ids:self.media[key]={'topic_key':job['topic_key'],'relevance_verified':False}
            self.selection[job['id']]=ids*2

    def test_pass_requires_exact_job_and_scene_selection(self):
        decision={'status':'PASS','issues':[],'selection':self.selection}
        self.assertEqual(c.parse_editorial_review(json.dumps(decision),self.jobs,self.media),decision)
        del decision['selection'][self.jobs[0]['id']]
        with self.assertRaises(ValueError):c.parse_editorial_review(json.dumps(decision),self.jobs,self.media)

    def test_fail_reason_codes_are_bounded_and_no_free_prose_allowed(self):
        issue={'code':'media_identity','job_index':0,'scene_index':1}
        decision={'status':'FAIL','issues':[issue],'selection':{}}
        self.assertEqual(c.parse_editorial_review(json.dumps(decision),self.jobs,self.media),decision)
        decision['issues']=[dict(issue,explanation='The supplied image depicts another person.')]
        self.assertIn('explanation',c.parse_editorial_review(json.dumps(decision),self.jobs,self.media)['issues'][0])
        for mutation in [dict(issue,code='private arbitrary prose'),dict(issue,job_index=True),dict(issue,scene_index=99),dict(issue,explanation={'private':'invalid type'})]:
            decision['issues']=[mutation]
            with self.assertRaises(ValueError):c.parse_editorial_review(json.dumps(decision),self.jobs,self.media)

    def test_review_failure_persists_diagnostics_without_scripts_or_media(self):
        decision={'status':'FAIL','issues':[{'code':'unsupported_claim','job_index':0,'scene_index':0,'explanation':'PRIVATE detailed claim explanation'}],'selection':{}}
        ledger={'days':{'2026-11-05':{'status':'staged_review_submitted','retry_hold':True}},'publications':[]}
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put',return_value='next'),patch.object(c.relevant_media,'candidates',return_value=self.media),patch.object(c,'openai',return_value=(json.dumps(decision),[{'url':'https://example.org'}])),patch.object(c.relevant_media,'apply_review') as apply,redirect_stdout(io.StringIO()) as log:
            with self.assertRaisesRegex(RuntimeError,'Editorial review did not pass'):c.review_jobs(ledger,'sha','2026-11-05',self.jobs)
            apply.assert_not_called()
            self.assertIn('unsupported_claim',log.getvalue())
        record=ledger['days']['2026-11-05']
        self.assertEqual(record['status'],'editorial_review_failed')
        self.assertTrue(record['retry_hold'])
        self.assertEqual(record['editorial_diagnostic']['issues'][0],{'code':'unsupported_claim','job_index':0,'scene_index':0})
        self.assertNotIn('PRIVATE',json.dumps(record))
        self.assertNotIn('jobs',record);self.assertNotIn('media',record)
        self.assertNotIn(self.jobs[0]['title'],json.dumps(record))

    def test_completed_day_with_legacy_hold_never_starts_work(self):
        day=c.p.now().date().isoformat()
        ledger={'days':{day:{'status':'complete','retry_hold':True}},'publications':[]}
        with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as check:
            c.produce();check.assert_not_called()

    def test_review_model_override_is_scoped_to_review(self):
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{}'}]}]}
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'test','OPENAI_MODEL':'draft-model','OPENAI_REVIEW_MODEL':'review-model'}),patch.object(c,'http',return_value=response) as api:
            c.openai('task',stage='editorial_review')
            self.assertEqual(api.call_args.args[1]['model'],'review-model')
            c.openai('task',stage='draft')
            self.assertEqual(api.call_args.args[1]['model'],'draft-model')

    def test_per_story_language_scope_and_private_explanations(self):
        ledger={'days':{'2026-11-05':{}},'publications':[]};requests=[]
        def provider(prompt,*args,**kwargs):
            job=json.loads(prompt.split('\nJOB:\n',1)[1].split('\nIMAGE METADATA:\n',1)[0]);requests.append((job,kwargs['instructions']))
            code='english_hook' if job['channel']=='ojirap' else 'source_inaccessible'
            return json.dumps({'status':'FAIL','issues':[{'code':code,'job_index':0,'scene_index':-1,'explanation':'PRIVATE exact missing evidence'}],'selection':{}}),[{'url':'https://example.org'}]
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put',return_value='sha'),patch.object(c.relevant_media,'candidates',return_value=self.media),patch.object(c,'openai',side_effect=provider) as api,redirect_stdout(io.StringIO()) as log:
            with self.assertRaisesRegex(RuntimeError,'Editorial review did not pass'):c.review_jobs(ledger,'sha','2026-11-05',self.jobs)
            self.assertEqual(api.call_count,4)
            details=(c.p.ROOT/'runs/2026-11-05/checkpoints/editorial-details.json').read_text()
            self.assertIn('PRIVATE exact missing evidence',details)
            self.assertNotIn('PRIVATE',log.getvalue())
        self.assertNotIn('PRIVATE',json.dumps(ledger))
        self.assertEqual([x['code'] for x in ledger['days']['2026-11-05']['editorial_diagnostic']['issues']],['source_inaccessible','source_inaccessible','review_contract','review_contract'])
        for job,instructions in requests:
            self.assertIn('ONLY the ONE',instructions)
            if job['channel']=='ojirap':self.assertIn('english_hook check does not apply',instructions)
            self.assertIn(c.REVIEW_CALIBRATION,instructions)

    def test_review_calibration_preserves_material_evidence_checks(self):
        self.assertIn(c.REVIEW_CALIBRATION,c.REVIEW_PROMPT)
        for requirement in ('material claims actually made','Omitting an unrelated biographical fact','Two independent adequate accessible authoritative sources','actual excerpt, source URL, observation date and attribution','If a material claim still lacks support','never waive material uncertainty'):
            self.assertIn(requirement,c.REVIEW_CALIBRATION)


class ProductionResume(unittest.TestCase):
    def test_media_outage_resumes_saved_research_and_draft_without_paid_repeats(self):
        day='2026-11-05';jobs=batch();media={};selection={}
        for index,job in enumerate(jobs):
            ids=[f'image-{index}-a',f'image-{index}-b']
            for key in ids:media[key]={'topic_key':job['topic_key'],'relevance_verified':False}
            selection[job['id']]=ids*2
        ledger={'days':{},'publications':[]}
        def save(value,sha):return 'next-sha'
        def provider(prompt,*args,**kwargs):
            stage=kwargs['stage']
            if stage=='research':return json.dumps(manifest_for(jobs)),evidence_for(manifest_for(jobs))
            if stage=='draft':return json.dumps({'jobs':jobs}),[]
            self.assertEqual(stage,'editorial_review')
            job=json.loads(prompt.split('\nJOB:\n',1)[1].split('\nIMAGE METADATA:\n',1)[0])
            return json.dumps({'status':'PASS','issues':[],'selection':{job['id']:selection[job['id']]}}),[{'url':'https://example.org/report'}]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c.p.write(root/'trends.json',{'topics':[]})
            with patch.object(c.p,'ROOT',root),patch.object(c.p,'now',return_value=dt.datetime(2026,11,5,12,tzinfo=dt.timezone(dt.timedelta(hours=9)))),patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'journal_put',side_effect=save),patch.object(c,'preflight'),patch.object(c,'collect_metrics'),patch.object(c.p,'collect',return_value={'path':str(root/'trends.json')}) as collect,patch.object(c,'openai',side_effect=provider) as api,patch.object(c.relevant_media,'candidates',side_effect=[RuntimeError('Commons unavailable'),media]),patch.object(c,'publish_reviewed') as publish,redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'Commons unavailable'):c.produce()
                self.assertEqual(ledger['days'][day]['status'],'draft_completed')
                self.assertEqual(api.call_count,2);publish.assert_not_called()
                c.produce()
                self.assertEqual(api.call_count,6);collect.assert_called_once();publish.assert_called_once()
                self.assertEqual(ledger['days'][day]['status'],'reviewed')
                self.assertFalse(ledger['days'][day]['retry_hold'])

    def test_new_provider_submission_markers_fail_closed_before_preflight(self):
        day=c.p.now().date().isoformat()
        for stage in ('draft','narration_correction','editorial_review'):
            ledger={'days':{day:{'status':stage+'_submitted'}},'publications':[]}
            with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as check,patch.object(c,'openai') as api:
                with self.assertRaisesRegex(RuntimeError,'reconcile'):c.produce()
                check.assert_not_called();api.assert_not_called()


if __name__=='__main__':unittest.main()
