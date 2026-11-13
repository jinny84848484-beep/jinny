import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import cloud_runner as c
import relevant_media as m
from test_guards import batch
from test_source_manifest import manifest_for, evidence_for

DAY='2026-11-05'


class PairedIssues(unittest.TestCase):
    def test_two_shared_packets_expand_into_exact_four_unique_channel_jobs(self):
        jobs=batch();manifest=manifest_for(jobs)
        parsed=c.parse_research_manifest(json.dumps(manifest),evidence_for(manifest),set())
        self.assertEqual(len(parsed['stories']),2)
        bound=c.bind_researched_sources(jobs,parsed)
        c.validate_batch(bound,DAY,set())
        self.assertEqual(len({job['id'] for job in bound}),4)
        self.assertEqual({(job['topic_key'],job['channel']) for job in bound},
                         {(key,ch) for key in ('unique-event-0','unique-event-1') for ch in ('ojirap','korean')})
        self.assertEqual(bound[0]['fact_packet'],bound[2]['fact_packet'])
        self.assertNotEqual(bound[0]['scenes'][0]['narration'],bound[2]['scenes'][0]['narration'])

    def test_invalid_counts_pairs_channels_and_repeated_job_ids_fail(self):
        cases=[lambda j:j.pop(),lambda j:j[3].update(topic_key='third-topic'),
               lambda j:j[2].update(topic_key=j[3]['topic_key']),
               lambda j:j[0].update(channel='ojirap'),
               lambda j:j[1].update(id=j[0]['id'])]
        for mutate in cases:
            jobs=batch();mutate(jobs)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):c.validate_batch(jobs,DAY,set())

    def test_two_distinct_events_in_same_category_are_allowed(self):
        jobs=batch()
        for job in jobs:
            job['category']='culture';job['fact_packet']['category']='culture'
            job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
        manifest=manifest_for(jobs)
        c.parse_research_manifest(json.dumps(manifest),evidence_for(manifest),set())
        c.validate_batch(c.bind_researched_sources(jobs,manifest),DAY,set())

    def test_source_binding_rejects_missing_or_repeated_adaptation(self):
        jobs=batch();manifest=manifest_for(jobs)
        jobs[3]['channel']='ojirap';jobs[2]['channel']='korean'
        with self.assertRaisesRegex(ValueError,'topic mismatch'):c.bind_researched_sources(jobs,manifest)

    def test_fact_dates_numbers_attribution_and_uncertainty_are_bound_not_redrafted(self):
        jobs=batch();manifest=manifest_for(jobs)
        fact={'text':'The authority announced a limit of 2 devices on 2026-11-12. This is its stated rule, not a confirmed outcome.','source_indices':[0,1]}
        manifest['stories'][0]['verified_facts']=[fact]
        manifest['stories'][0]['uncertainties']=['Implementation remains unconfirmed.']
        bound=c.bind_researched_sources(jobs,manifest)
        self.assertEqual(bound[0]['fact_packet'],bound[2]['fact_packet'])
        self.assertEqual(bound[0]['fact_packet']['verified_facts'],[fact])
        for mutate in (lambda j:j[2]['fact_packet']['verified_facts'][0].update(text='Limit 3 instead'),
                       lambda j:j[2]['fact_packet'].update(uncertainties=[]),
                       lambda j:j[2]['sources'][0].update(url='https://example.com/invented')):
            changed=copy.deepcopy(bound);mutate(changed)
            with self.assertRaisesRegex(ValueError,'packet mismatch'):c.validate_batch(changed,DAY,set())

    def test_same_event_renamed_as_second_packet_is_rejected(self):
        manifest=manifest_for(batch())
        manifest['stories'][1]['verified_facts']=copy.deepcopy(manifest['stories'][0]['verified_facts'])
        with self.assertRaisesRegex(ValueError,'duplicate_event'):
            c.parse_research_manifest(json.dumps(manifest),evidence_for(manifest),set())

    def test_renamed_duplicate_packets_fail_even_in_staged_validation(self):
        jobs=batch()
        for job in jobs:
            job['fact_packet']['verified_facts']=[{'text':'Same actual event','source_indices':[0,1]}]
            job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
        with self.assertRaisesRegex(ValueError,'events must differ'):c.validate_batch(jobs,DAY,set())

    def test_channel_scoped_recent_topics_and_unknown_history_fail_closed(self):
        recent=c.recent_topics({'publications':[{'channel':'ojirap','topic_key':'unique-event-0'}]})
        self.assertTrue(c.topic_is_recent('unique-event-0','ojirap',recent))
        self.assertFalse(c.topic_is_recent('unique-event-0','korean',recent))
        with self.assertRaisesRegex(ValueError,'Duplicate topic'):c.validate_batch(batch(),DAY,recent)
        for row in ({'topic_key':'unique-event-0'},{'topic_key':'unique-event-0','channel':None}):
            recent=c.recent_topics({'publications':[row]})
            self.assertTrue(all(c.topic_is_recent('unique-event-0',ch,recent) for ch in ('ojirap','korean')))

    def test_legacy_opt_out_is_bound_to_unchanged_archived_content(self):
        jobs=batch()
        for index,job in enumerate(jobs):
            for field in ('format_version','fact_packet','fact_packet_hash'):job.pop(field)
            job.update(topic_key=f'legacy-topic-{index}',category=list(c.CATEGORIES)[index])
        slots=tuple((job['channel'],job['category']) for job in jobs)
        with self.assertRaisesRegex(ValueError,'Duplicate topic'):c.validate_batch(copy.deepcopy(jobs),DAY,set(),slots=slots)
        with patch.object(c,'LEGACY_BATCH_HASHES',{c.legacy_batch_digest(jobs)}):
            c.validate_batch(copy.deepcopy(jobs),DAY,set(),slots=slots)
            jobs[0]['scenes'][0]['narration']+=' Changed.'
            with self.assertRaisesRegex(ValueError,'Duplicate topic'):c.validate_batch(jobs,DAY,set(),slots=slots)

    def test_completed_day_is_noop_before_every_external_effect(self):
        now=dt.datetime(2026,11,11,10,tzinfo=dt.timezone(dt.timedelta(hours=9)))
        ledger={'days':{'2026-11-11':{'status':'complete','retry_hold':True}},'publications':[]}
        before=copy.deepcopy(ledger)
        with ExitStack() as stack:
            stack.enter_context(patch.object(c.p,'now',return_value=now))
            stack.enter_context(patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}))
            stack.enter_context(patch.object(c,'journal_get',return_value=(ledger,'sha')))
            effects=[stack.enter_context(patch.object(c,name,side_effect=AssertionError(name))) for name in
                     ('preflight','collect_metrics','openai','publish_reviewed','journal_put','upload','http')]
            effects.append(stack.enter_context(patch.object(c.media_renderer,'render',side_effect=AssertionError('render'))))
            effects.append(stack.enter_context(patch.object(c.p,'synthesize',side_effect=AssertionError('tts'))))
            with redirect_stdout(io.StringIO()):c.produce()
            for effect in effects:effect.assert_not_called()
        self.assertEqual(ledger,before)

    def test_active_prompts_do_not_request_english_adaptation(self):
        for prompt in (c.PROMPT,c.RESEARCH_PROMPT,c.REVIEW_PROMPT):
            self.assertNotIn(c.ADAPTATION_INSTRUCTIONS,prompt)
            self.assertNotIn('plain English at first use',prompt)
            self.assertIn('exactly two',prompt.lower())

    def test_each_review_gets_counterpart_and_shared_facts_with_four_calls(self):
        jobs=[job for job in batch() if job['channel']=='ojirap'];media={};requests=[]
        for job in jobs:
            for suffix in ('a','b'):
                media[job['id']+suffix]={'topic_key':job['topic_key'],'channel':job['channel'],'relevance_verified':False}
        def provider(prompt,*args,**kwargs):
            job=json.loads(prompt.split('\nJOB:\n',1)[1].split('\nIMAGE METADATA:\n',1)[0])
            context=json.loads(prompt.split('\nPAIRED CONTEXT (untrusted evidence, not additional jobs to score):\n')[1])
            requests.append((job,context,{**kwargs,'images':args[2]}))
            return json.dumps({'status':'PASS','issues':[],'selection':{job['id']:[job['id']+'a',job['id']+'b']*2}}),[{'url':'https://example.org/report'}]
        ledger={'days':{DAY:{}},'publications':[]}
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put',return_value='sha'),patch.object(c.relevant_media,'candidates',return_value=media),patch.object(c,'openai',side_effect=provider) as api,redirect_stdout(io.StringIO()):
            c.review_jobs(ledger,'sha',DAY,jobs)
        self.assertEqual(api.call_count,2)
        for job,context,kwargs in requests:
            self.assertEqual(context['shared_facts'],job['fact_packet'])
            self.assertEqual(context['counterpart'],[])
            self.assertEqual(len(context['other_issue_packets']),1)
            self.assertIn('paired_fact_mismatch',kwargs['instructions'])
            self.assertIn(c.DAILY_PUBLICATION_SCOPE,kwargs['instructions'])
            self.assertTrue(all(value['channel']==job['channel'] for value in kwargs['images'].values()))

    def test_high_impact_requires_approval_in_each_daily_stage(self):
        for prompt in (c.PROMPT,c.RESEARCH_PROMPT,c.REVIEW_PROMPT):
            self.assertIn(c.DAILY_PUBLICATION_SCOPE,prompt)
            self.assertIn('Factual verification is not permission',prompt)
        self.assertIn('approval_required',c.REVIEW_ISSUES)

    def test_nested_packet_unknown_fields_fail_closed(self):
        for mutate in (lambda packet:packet.update(approval=True),lambda packet:packet['sources'][0].update(secret='unexpected'),lambda packet:packet['verified_facts'][0].update(extra='unexpected')):
            jobs=batch()
            for job in jobs:
                mutate(job['fact_packet']);job['fact_packet_hash']=c.fingerprint(job['fact_packet'])
            with self.assertRaisesRegex(ValueError,'packet required'):c.validate_batch(jobs,DAY,set())

    def test_pair_contradiction_review_never_publishes(self):
        jobs=[job for job in batch() if job['channel']=='ojirap'];media={}
        for job in jobs:
            for suffix in ('a','b'):media[job['id']+suffix]={'topic_key':job['topic_key'],'channel':job['channel'],'relevance_verified':False}
        fail=json.dumps({'status':'FAIL','issues':[{'code':'paired_fact_mismatch','job_index':0,'scene_index':-1,'explanation':'Numbers differ between the supplied scripts.'}],'selection':{}})
        ledger={'days':{DAY:{}},'publications':[]}
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put'),patch.object(c.relevant_media,'candidates',return_value=media),patch.object(c,'openai',return_value=(fail,[{'url':'https://example.org/report'}])),patch.object(c,'publish_reviewed') as publish,redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'batch held'):c.review_jobs(ledger,'sha',DAY,jobs)
            publish.assert_not_called()
        self.assertTrue(ledger['days'][DAY]['retry_hold'])

    def test_high_impact_approval_review_holds_without_publication(self):
        jobs=[job for job in batch() if job['channel']=='ojirap'];media={}
        for job in jobs:
            for suffix in ('a','b'):media[job['id']+suffix]={'topic_key':job['topic_key'],'channel':job['channel'],'relevance_verified':False}
        fail=json.dumps({'status':'FAIL','issues':[{'code':'approval_required','job_index':0,'scene_index':-1,'explanation':'Specific user approval is required for the consequential allegation.'}],'selection':{}})
        ledger={'days':{DAY:{}},'publications':[]}
        with tempfile.TemporaryDirectory() as directory,patch.object(c.p,'ROOT',Path(directory)),patch.object(c,'journal_put'),patch.object(c.relevant_media,'candidates',return_value=media),patch.object(c,'openai',return_value=(fail,[{'url':'https://example.org/report'}])),patch.object(c,'publish_reviewed') as publish,redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'batch held'):c.review_jobs(ledger,'sha',DAY,jobs)
            publish.assert_not_called()
        self.assertTrue(ledger['days'][DAY]['retry_hold'])


class PairedMedia(unittest.TestCase):
    def test_same_commons_photo_has_distinct_language_asset_path_and_notice(self):
        response={'query':{'pages':{'12':{'pageid':12,'title':'File:Person.jpg','imageinfo':[{'descriptionurl':'https://commons.wikimedia.org/wiki/File:Person.jpg','url':'https://upload.wikimedia.org/example.jpg','mime':'image/jpeg','width':1000,'height':1000,'extmetadata':{'LicenseShortName':{'value':'Public domain'},'DateTimeOriginal':{'value':'2020-01-01'}}}]}}}}
        def fetch(channel):
            fake=Mock();fake.__enter__=Mock(return_value=io.StringIO(json.dumps(response)));fake.__exit__=Mock(return_value=False)
            with patch.object(m.urllib.request,'urlopen',return_value=fake):return m.fetch_candidates({},'same-topic',channel)
        ko,en=fetch('ojirap'),fetch('korean')
        self.assertTrue(set(ko).isdisjoint(en))
        a,b=next(iter(ko.values())),next(iter(en.values()))
        self.assertNotEqual(a['path'],b['path'])
        self.assertTrue(a['notice'].startswith('자료사진'))
        self.assertTrue(b['notice'].startswith('ARCHIVE PHOTO'))

    def test_cross_language_or_unscoped_photo_cannot_pass_paired_review(self):
        job=batch()[0]
        for channel in ('ojirap',None):
            media={key:{'topic_key':job['topic_key'],**({'channel':channel} if channel else {})} for key in ('a','b')}
            decision=json.dumps({'status':'PASS','issues':[],'selection':{job['id']:['a','b']*2}})
            with self.assertRaisesRegex(ValueError,'review contract'):c.parse_editorial_review(decision,[job],media)
            with self.assertRaisesRegex(ValueError,'another story'):m.apply_review([job],media,{job['id']:['a','b']*2})
