import base64
import copy
import hashlib
import io
import json
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch
import cloud_runner as c
import staged_runner as r
import staged_transfer as s
from test_guards import batch

DAY='2026-11-05'
ENV={'GITHUB_REPOSITORY':r.REPOSITORY,'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_ACTIONS':'true','PUBLIC_UPLOADS_ENABLED':'true','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_ACTOR':'jinny84848484-beep','GITHUB_ACTOR_ID':'341766743','GITHUB_TRIGGERING_ACTOR':'jinny84848484-beep'}

def valid_jobs():
    jobs=batch()
    for job in jobs:job.update(visual_queries=['exact subject one','exact subject two'],description='Public ready description')
    return jobs

class StagedRunner(unittest.TestCase):
    def setUp(self):
        import datetime as dt
        self.signer=s.Ed25519PrivateKey.generate()
        clock=patch.object(c.p,'now',return_value=dt.datetime(2026,11,5,12,tzinfo=dt.timezone(dt.timedelta(hours=9))))
        clock.start();self.addCleanup(clock.stop)
    def setup_run(self, stack, jobs=None):
        ledger={'days':{DAY:{'status':'draft_validation_failed','retry_hold':True,'draft_failures':[{'reason':'old'}]}},'publications':[{'job_id':'old','topic_key':'old'}]}
        receiver=s.create_receiver(r.REPOSITORY,'123','1',DAY,s.public(self.signer))
        payload=s.seal_batch(receiver.announcement,{'jobs':valid_jobs() if jobs is None else jobs},self.signer)
        stack.enter_context(patch.dict(r.os.environ,{**ENV,'STAGED_SENDER_PUBLIC_KEY':s.public(self.signer)}))
        stack.enter_context(patch.object(r,'create_receiver',return_value=receiver))
        get=stack.enter_context(patch.object(c,'journal_get',side_effect=lambda:(copy.deepcopy(ledger),'sha')))
        def save(value,sha):
            ledger.clear();ledger.update(copy.deepcopy(value));return 'next-sha'
        put=stack.enter_context(patch.object(c,'journal_put',side_effect=save))
        fetch=stack.enter_context(patch.object(r,'fetch_envelope',return_value=(payload,'blob')))
        preflight=stack.enter_context(patch.object(c,'preflight'))
        review=stack.enter_context(patch.object(c,'review_jobs',return_value='reviewed-sha'))
        publish=stack.enter_context(patch.object(c,'publish_reviewed'))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return ledger,receiver,put,fetch,preflight,review,publish,get

    def test_success_orders_validation_marker_review_publish_preserves_history(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            def check_review(current,sha,day,jobs,**kwargs):
                self.assertEqual(current['days'][day]['status'],'staged_review_submitted')
                self.assertTrue(current['days'][day]['retry_hold'])
                publish.assert_not_called();return 'reviewed-sha'
            review.side_effect=check_review
            r.run_staged()
            self.assertEqual(put.call_count,2);preflight.assert_called_once();review.assert_called_once();publish.assert_called_once()
            self.assertEqual(ledger['publications'],[{'job_id':'old','topic_key':'old'}])
            self.assertEqual(ledger['days'][DAY]['draft_failures'],[{'reason':'old'}])
            self.assertNotIn('jobs',ledger['days'][DAY])
            self.assertIsNone(receiver._private)

    def test_invalid_sources_never_call_preflight_review_or_publish(self):
        jobs=valid_jobs();jobs[0]['sources']=[]
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack,jobs)
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            preflight.assert_not_called();review.assert_not_called();publish.assert_not_called()
            self.assertTrue(ledger['days'][DAY]['retry_hold'])
            self.assertNotIn('Public ready',json.dumps(ledger))

    def test_review_failure_never_publishes_and_marker_blocks_replay(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            review.side_effect=RuntimeError('PRIVATE DRAFT')
            with self.assertRaisesRegex(RuntimeError,'^Staged attempt stopped; hold remains, reconcile journal$'):r.run_staged()
            publish.assert_not_called()
            self.assertEqual(ledger['days'][DAY]['status'],'staged_review_submitted')
            with self.assertRaisesRegex(RuntimeError,'no replay'):r.run_staged()
            self.assertEqual(review.call_count,1)

    def test_timeout_keeps_hold_without_paid_operations(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            receiver.deadline=-1
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            fetch.assert_not_called();preflight.assert_not_called();review.assert_not_called();publish.assert_not_called()
            self.assertEqual(ledger['days'][DAY]['status'],'staged_waiting')

    def test_initial_write_failure_never_exposes_key_or_reads_envelope(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            put.side_effect=RuntimeError('write result uncertain')
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            fetch.assert_not_called();review.assert_not_called();publish.assert_not_called()
            self.assertEqual(ledger['days'][DAY]['status'],'draft_validation_failed')

    def test_changed_ledger_after_preflight_stops_before_review(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            preflight.side_effect=lambda:ledger['days'][DAY].update(status='upload_submitted')
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            review.assert_not_called();publish.assert_not_called()

    def test_normal_produce_blocks_staged_markers_even_if_hold_missing(self):
        for status in ['staged_waiting','staged_review_submitted']:
            ledger={'days':{DAY:{'status':status}},'publications':[]}
            with patch.dict(c.os.environ,ENV),patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as check:
                with self.assertRaisesRegex(RuntimeError,'reconcile'):c.produce()
                check.assert_not_called()

    def test_context_rejects_fork_schedule_wrong_branch_and_gate(self):
        for field,value in [('GITHUB_REPOSITORY','attacker/fork'),('GITHUB_EVENT_NAME','schedule'),('GITHUB_REF','refs/heads/test'),('PUBLIC_UPLOADS_ENABLED','false')]:
            with patch.dict(r.os.environ,{**ENV,field:value}),patch.object(c,'journal_get') as read:
                with self.assertRaises(RuntimeError):r.run_staged()
                read.assert_not_called()

    def test_fetch_pins_blob_and_never_uses_download_url(self):
        payload=b'encrypted';sha=hashlib.sha1(b'blob 9\0'+payload).hexdigest()
        response={'type':'file','encoding':'base64','size':len(payload),'content':base64.b64encode(payload).decode(),'sha':sha,'download_url':'https://attacker.invalid'}
        with patch.dict(r.os.environ,{'GITHUB_TOKEN':'test'}),patch.object(c,'http',return_value=response) as request:
            self.assertEqual(r.fetch_envelope('state/staging/123-1.json'),(payload,sha))
            self.assertIn('/contents/state/staging/123-1.json?ref=main',request.call_args.args[0])
            response['sha']='wrong'
            with self.assertRaisesRegex(RuntimeError,'metadata rejected'):r.fetch_envelope('state/staging/123-1.json')

    def test_film_music_plan_passes_with_diversity_and_balance(self):
        jobs=valid_jobs()
        for job in jobs:
            job['category']='film' if job['topic_key']=='unique-event-0' else 'music'
            job['fact_packet']['category']=job['category'];job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
        plan=r.validate_staged(jobs,DAY,{'publications':[]})
        self.assertIn(('korean','film'),plan);self.assertIn(('korean','music'),plan)
        jobs[1]['category']='film'
        with self.assertRaisesRegex(ValueError,'categories must match'):r.validate_staged(jobs,DAY,{'publications':[]})

    def test_selected_plan_reaches_review_and_publish(self):
        jobs=valid_jobs()
        for job in jobs:
            job['category']='film' if job['topic_key']=='unique-event-0' else 'music'
            job['fact_packet']['category']=job['category'];job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack,jobs)
            r.run_staged()
            self.assertEqual(review.call_args.kwargs['slots'],publish.call_args.kwargs['slots'])
            self.assertIn(('korean','music'),review.call_args.kwargs['slots'])

    def test_review_instruction_uses_selected_plan_not_fixed_baseline(self):
        plan=(('korean','film'),('korean','music'),('ojirap','technology'),('ojirap','entertainment'))
        with patch.object(c,'openai',return_value=('PASS',[])) as api:
            c.guarded_research({},'sha',DAY,'editorial_review','review',True,editorial_slots=plan)
        instructions=api.call_args.kwargs['instructions']
        self.assertIn('korean/film',instructions)
        self.assertNotIn('korean/politics',instructions)
        self.assertIn('Independently audit',instructions)

    def test_submission_write_failure_never_calls_paid_review(self):
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            original=put.side_effect
            def fail_second(value,sha):
                if value['days'][DAY]['status']=='staged_review_submitted':raise RuntimeError('uncertain save')
                return original(value,sha)
            put.side_effect=fail_second
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            review.assert_not_called();publish.assert_not_called()
            self.assertTrue(ledger['days'][DAY]['retry_hold'])

    def test_unknown_fields_rejected_at_every_nested_object(self):
        mutations=[lambda j:j[0].update(private='CANARY'),lambda j:j[0]['sources'][0].update(private='CANARY'),lambda j:j[0]['scenes'][0].update(private='CANARY'),lambda j:j[0].update(experiment={'private':'CANARY'}),lambda j:j[0].update(experiment={'hook_type':{'private':'CANARY'}})]
        for mutate in mutations:
            jobs=valid_jobs();mutate(jobs)
            with self.assertRaises(ValueError):r.validate_staged(jobs,DAY,{'publications':[]})

    def test_offline_validation_does_not_fabricate_verification_stamps(self):
        jobs=valid_jobs();before=copy.deepcopy(jobs)
        r.validate_staged(jobs,DAY,{'publications':[]})
        self.assertEqual(jobs,before)
        for key in ['editorial_status','verified_at','channel_id']:
            jobs=valid_jobs();jobs[0][key]='verified'
            with self.assertRaises(ValueError):r.validate_staged(jobs,DAY,{'publications':[]})
        jobs=valid_jobs();jobs[0]['sources'][0]['checked_at']='2026-11-05'
        with self.assertRaises(ValueError):r.validate_staged(jobs,DAY,{'publications':[]})

    def test_actor_identity_is_pinned_not_commit_author_email(self):
        for field,value in [('GITHUB_ACTOR','other'),('GITHUB_ACTOR_ID','1'),('GITHUB_TRIGGERING_ACTOR','other')]:
            with patch.dict(r.os.environ,{**ENV,'STAGED_SENDER_PUBLIC_KEY':s.public(self.signer),field:value}):
                with self.assertRaisesRegex(RuntimeError,'context not authorized'):r.run_context()

    def test_midnight_transition_rejects_before_review(self):
        import datetime as dt
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            original=c.p.now
            switched=False
            def after_preflight():
                nonlocal switched
                switched=True
            def now():return original()+dt.timedelta(days=1) if switched else original()
            stack.enter_context(patch.object(c.p,'now',side_effect=now))
            preflight.side_effect=after_preflight
            with self.assertRaisesRegex(RuntimeError,'hold remains'):r.run_staged()
            review.assert_not_called();publish.assert_not_called()

    def test_full_pass_to_publication_uses_real_helpers_mocking_external_io(self):
        import tempfile
        from pathlib import Path
        actual_review=c.review_jobs;actual_publish=c.publish_reviewed
        with ExitStack() as stack:
            ledger,receiver,put,fetch,preflight,review,publish,get=self.setup_run(stack)
            review.side_effect=actual_review;publish.side_effect=actual_publish
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(patch.object(c.p,'ROOT',root))
            jobs=valid_jobs();media={};selection={}
            for index,job in enumerate(jobs):
                names=[f'image-{index}-a',f'image-{index}-b']
                for name in names:media[name]={'topic_key':job['topic_key'],'channel':job['channel'],'relevance_verified':False}
                selection[job['id']]=names*2
            stack.enter_context(patch.object(c.relevant_media,'candidates',return_value=media))
            def provider(prompt,*args,**kwargs):
                self.assertNotIn('"editorial_status"',prompt)
                self.assertNotIn('"checked_at"',prompt)
                self.assertEqual(kwargs['stage'],'editorial_review')
                job=json.loads(prompt.split('\nJOB:\n',1)[1].split('\nIMAGE METADATA:\n',1)[0])
                return 'PASS\n'+json.dumps({job['id']:selection[job['id']]}),[{'url':'https://example.org/report'}]
            stack.enter_context(patch.object(c,'openai',side_effect=provider))
            stack.enter_context(patch.object(c,'download_media'))
            render=stack.enter_context(patch.object(c.media_renderer,'render'))
            stack.enter_context(patch.object(c,'youtube_token',return_value='test'))
            verify=stack.enter_context(patch.object(c,'verify_channel'))
            upload=stack.enter_context(patch.object(c,'upload',side_effect=['video1','video2','video3','video4']))
            r.run_staged()
            self.assertEqual(render.call_count,4);self.assertEqual(upload.call_count,4);self.assertEqual(verify.call_count,4)
            self.assertEqual(ledger['days'][DAY]['status'],'complete')
            self.assertEqual(len(ledger['publications']),5)
            self.assertFalse(ledger['days'][DAY]['retry_hold'])
            self.assertTrue(all(j['editorial_status']=='verified' for j in ledger['days'][DAY]['jobs']))


class StagedFailureLabel(unittest.TestCase):
    def test_label_prints_stage_and_fixed_message(self):
        label=r.staged_failure_label('editorial_review',RuntimeError('Editorial review did not pass; batch held'))
        self.assertEqual(label,'Staged failure detail: stage=editorial_review type=RuntimeError message=Editorial review did not pass; batch held')

    def test_label_hides_runtime_data_and_unknown_stage(self):
        secret='sk-live-should-not-appear https://example.com/private?token=abc'
        label=r.staged_failure_label('somewhere',ValueError(secret))
        self.assertNotIn('sk-live',label);self.assertNotIn('token',label)
        self.assertIn('stage=unknown_stage',label);self.assertIn('message=unlisted_message',label)

    def test_review_failure_logs_stage_without_releasing_hold(self):
        with ExitStack() as stack:
            out=io.StringIO()
            ledger,receiver,put,fetch,preflight,review,publish,get=StagedRunner.setup_run(self_stub(self),stack)
            stack.enter_context(redirect_stdout(out))
            review.side_effect=RuntimeError('Editorial review did not pass; batch held')
            with self.assertRaisesRegex(RuntimeError,'Staged attempt stopped; hold remains'):r.run_staged()
            self.assertIn('stage=editorial_review type=RuntimeError message=Editorial review did not pass; batch held',out.getvalue())
            self.assertEqual(ledger['days'][DAY]['status'],'staged_review_submitted')
            self.assertTrue(ledger['days'][DAY]['retry_hold'])
            publish.assert_not_called()


def self_stub(case):
    import datetime as dt
    stub=StagedRunner('test_success_orders_validation_marker_review_publish_preserves_history')
    stub.signer=s.Ed25519PrivateKey.generate()
    clock=patch.object(c.p,'now',return_value=dt.datetime(2026,11,5,12,tzinfo=dt.timezone(dt.timedelta(hours=9))))
    clock.start();case.addCleanup(clock.stop)
    return stub

