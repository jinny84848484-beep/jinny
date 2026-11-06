import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import additional_batch as a
import additional_driver as driver
import cloud_runner as c
import original_graphics as graphics
from test_additional_batch import fixtures, FakeRuntime
from test_original_graphics import JOB, SPEC

OLD='2026-11-06'; NEW='2026-11-07'


class NextDayContinuation(unittest.TestCase):
    def setUp(self):
        self.packet,self.approval,self.ledger,self.config=fixtures()
        self.packet.update(production_date=NEW, completed_anchor_day=OLD, batch_id=NEW+'-extra-01')
        self.packet['job']['id']=NEW+'-extra-01-stalking-protection'
        self.approval['completed_anchor']={'day':OLD,'record_hash':a.digest(self.ledger['days'][OLD])}
        self.approval['prior_tts_audit'].update(production_date=NEW,scope='repository_pipeline',entries=[])
        self.sign()

    def sign(self):
        self.approval['packet_hash']=a.digest(self.packet)
        self.approval['publication']['job_hash']=a.digest(self.packet['job'])
        self.approval['prior_tts_audit']['historical_identity']=a.historical_identity(self.ledger)

    def validate(self):
        return a.validate(self.packet,self.approval,a.digest(self.approval),self.ledger,self.config,NEW)

    def test_before_scheduled_batch_preserves_both_day_records(self):
        original=copy.deepcopy(self.ledger['days'])
        runtime=FakeRuntime(self.ledger,self.config);runtime.date=NEW
        a.run(self.packet,self.approval,a.digest(self.approval),runtime)
        self.assertEqual(runtime.ledger['days'],original)
        self.assertNotIn(NEW,runtime.ledger['days'])
        record=runtime.ledger['additional_batches'][self.packet['batch_id']]
        self.assertEqual(record['production_date'],NEW);self.assertEqual(record['completed_anchor_day'],OLD)
        self.assertEqual(record['tts_reservation'],250)

    def test_existing_new_day_research_hold_is_not_cleared(self):
        self.ledger['days'][NEW]={'status':'research_validation_failed','retry_hold':True,'checkpoints':{'research':{'status':'completed'}}}
        self.sign();runtime=FakeRuntime(self.ledger,self.config);runtime.date=NEW
        a.run(self.packet,self.approval,a.digest(self.approval),runtime)
        self.assertEqual(runtime.ledger['days'],self.ledger['days'])

    def test_anchor_requires_exact_explicit_approval(self):
        for change in ('missing','changed'):
            value=copy.deepcopy(self.approval)
            if change=='missing':value.pop('completed_anchor')
            else:value['completed_anchor']['record_hash']='0'*64
            with self.assertRaisesRegex(ValueError,'anchor approval'):
                a.validate(self.packet,value,a.digest(value),self.ledger,self.config,NEW)

    def test_future_or_incomplete_anchor_rejected(self):
        self.packet['completed_anchor_day']='2026-11-08';self.sign()
        with self.assertRaisesRegex(ValueError,'future'):self.validate()
        self.packet['completed_anchor_day']=OLD;self.ledger['days'][OLD]['status']='reviewed';self.sign()
        with self.assertRaisesRegex(ValueError,'not complete'):self.validate()

    def test_old_day_usage_not_reused_as_actual_day_audit(self):
        self.assertEqual(self.validate()['prior_characters'],0)
        self.approval['prior_tts_audit']['production_date']=OLD
        with self.assertRaisesRegex(ValueError,'prior TTS audit'):self.validate()

    def test_repository_scope_explicit_not_universal_claim(self):
        self.approval['prior_tts_audit'].pop('scope')
        with self.assertRaisesRegex(ValueError,'repository usage audit'):self.validate()

    def test_actual_day_pending_tts_must_be_accounted(self):
        job=copy.deepcopy(self.ledger['days'][OLD]['jobs'][0]);job.update(id=NEW+'-pending-other',topic_key='other-story')
        self.ledger['days'][NEW]={'status':'tts_submitted','active_job':job['id'],'jobs':[job]}
        self.sign()
        with self.assertRaisesRegex(ValueError,'usage omitted'):self.validate()
        self.approval['prior_tts_audit']['entries']=[{'attempt_id':'pending-1','job_id':job['id'],'characters':300,
             'includes_delivery_prefix':True,'evidence_hash':'a'*64}]
        self.assertEqual(self.validate()['day_total_reserved'],550)

    def test_old_additional_reservation_not_charged_to_new_day(self):
        self.ledger['additional_batches']={'old':{'production_date':OLD,'status':'complete','tts_reservation':5500}}
        self.assertEqual(self.validate()['prior_characters'],0)

    def test_recent_topic_duplicate_still_rejected(self):
        self.packet['event_keys'].append('old-story');self.sign()
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.validate()

    def test_session_uses_anchor_without_creating_scheduled_day(self):
        original=copy.deepcopy(self.ledger['days'])
        driver.register_session(self.ledger,'123',{'run_id':'123'},NEW,self.packet['batch_id'],a.digest(self.approval),completed_anchor_day=OLD)
        self.assertEqual(self.ledger['days'],original)
        self.assertEqual(self.ledger['additional_sessions']['123']['completed_anchor_day'],OLD)

    def test_bootstrap_prior_confirmation_is_not_invented_upload_time(self):
        legacy={'job_id':'bootstrap-old','topic_key':'bootstrap-topic','confirmed_at':'2026-11-05T03:00:00+09:00'}
        self.ledger['publications'].insert(0,legacy);self.sign()
        self.assertEqual(self.validate()['prior_characters'],0)
        self.assertNotIn('at',legacy)

    def test_unknown_legacy_date_requires_accounting_not_zero(self):
        self.ledger['publications'].insert(0,{'job_id':'undated-legacy','topic_key':'other'})
        self.sign()
        with self.assertRaisesRegex(ValueError,'usage omitted'):self.validate()

    def test_production_date_still_expires(self):
        with self.assertRaisesRegex(ValueError,'date expired'):
            a.validate(self.packet,self.approval,a.digest(self.approval),self.ledger,self.config,'2026-11-08')


class DailyForwardBudget(unittest.TestCase):
    def setUp(self):
        p,ap,l,self.config=fixtures();self.jobs=l['days'][OLD]['jobs']
    def test_no_extra_preserves_default_behavior(self):
        with patch.object(c.p,'cfg',side_effect=AssertionError('Should not inspect new path')):
            c.assert_additional_day_budget({'additional_batches':{'old':{'production_date':OLD,'tts_reservation':99999}}},NEW,self.jobs)
    def test_same_day_reserved_and_uncertain_counts_once(self):
        ledger={'additional_batches':{'extra':{'production_date':NEW,'tts_reservation':5000,'status':'tts_submitted'}}}
        with patch.object(c.p,'cfg',return_value=self.config):
            c.assert_additional_day_budget(ledger,NEW,self.jobs) # four250 +5000 exactly6000
            ledger['additional_batches']['extra']['tts_reservation']=5001
            with self.assertRaisesRegex(RuntimeError,'Aggregate daily'):c.assert_additional_day_budget(ledger,NEW,self.jobs)
    def test_unresolved_extra_stops_regular_before_paid_preflight(self):
        ledger={'days':{NEW:{}},'publications':[],
                'additional_batches':{'extra':{'production_date':NEW,'status':'upload_submitted','tts_reservation':368}}}
        with patch.dict(c.os.environ,{'PUBLIC_UPLOADS_ENABLED':'true'}), \
             patch.object(c,'journal_get',return_value=(ledger,'sha')),patch.object(c,'preflight') as preflight:
            with self.assertRaisesRegex(RuntimeError,'Unresolved additional'):c.produce()
            preflight.assert_not_called()
        ledger['additional_batches']['extra']['status']='complete'
        c.assert_no_unresolved_additional(ledger)

    def test_unknown_extra_reservation_blocks(self):
        with self.assertRaisesRegex(RuntimeError,'unknown'):
            c.assert_additional_day_budget({'additional_batches':{'x':{'production_date':NEW}}},NEW,self.jobs)


class NextDayGraphics(unittest.TestCase):
    def test_same_editorial_new_id_and_truthful_production_label(self):
        job=copy.deepcopy(JOB);job['id']=NEW+'-extra-01-neutral'
        editorial={k:v for k,v in job.items() if k not in ('id','channel_id')}
        with patch.object(graphics,'EDITORIAL_HASH',a.digest(editorial)),tempfile.TemporaryDirectory() as temp:
            manifest=graphics.build_original_graphics(job,Path(temp),approved_story_hash=a.digest(job),production_date=NEW,graphics_spec=SPEC)
            self.assertEqual(manifest['production_date'],NEW)
            labels=[label['text'] for label in manifest['cards'][0]['labels']]
            self.assertTrue(any('제작 2026.11.07' in text for text in labels))
            self.assertFalse(any('2026.11.07 저녁 보도' in text for text in labels))
            self.assertEqual(manifest['editorial_hash'],a.digest(editorial))
            job['id']='2026-11-08-extra-01-neutral'
            with self.assertRaises(ValueError):
                graphics.build_original_graphics(job,Path(temp),approved_story_hash=a.digest(job),production_date='2026-11-08',graphics_spec=SPEC)

if __name__=='__main__':unittest.main()
