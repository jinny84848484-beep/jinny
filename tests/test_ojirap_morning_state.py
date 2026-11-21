from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from ojirap_ledger_adapter import GitHubLedgerCAS
from ojirap_morning_schedule import CHANNEL_ID, Job, ProcessingPending, ReleaseWindow, ScheduleHold, stage_and_schedule
from ojirap_morning_state import MANIFESTS, MorningState
from ojirap_readiness_poll import poll_resume
import ojirap_slot_cas as slots


class BridgeTests(TestCase):
    def setUp(self):
        self.ledger = dict(days={},publications=[])
        self.rev=1
        self.adapter=GitHubLedgerCAS(self.get,self.put)
        self.window=ReleaseWindow(date(2026,11,21))
        self.clock=lambda:self.window.start
        self.state=self.worker()
        self.jobs=tuple(Job(str(i),str(i),str(i)*64,CHANNEL_ID,True,True) for i in (1,2))
        self.ids=('a'*11,'b'*11)
        self.ready=False;self.booked={}
        self.upload=Mock(side_effect=self.ids)
        self.arm=Mock(side_effect=self.arm_fn)

    def worker(self, owner="run1"):
        return MorningState(self.adapter,self.window,owner,clock=self.clock)
    def get(self):
        return deepcopy(self.ledger), f"{self.rev:040x}"
    def put(self, ledger, rev):
        if rev!=self.get()[1]: raise RuntimeError("HTTP 409")
        self.rev+=1
        self.ledger=deepcopy(ledger)
        return self.get()[1]
    def read_id(self,vid):
        return dict(id=vid,channel_id=CHANNEL_ID,privacy="private",ever_public=False,
                    publish_at=self.booked.get(vid),processing_status="succeeded" if self.ready else "processing")
    def arm_fn(self,vid,when):
        self.booked[vid]=when
    def stage(self):
        return stage_and_schedule(self.window,self.jobs,clock=self.clock,reserve=self.state.reserve,
            checkpoint=self.state.checkpoint,upload_private=self.upload,
            read_youtube=self.read_id,schedule_existing=self.arm)
    def resume(self, worker):
        return poll_resume(self.window,self.jobs,"run1",clock=self.clock,
            load=worker.load,checkpoint=worker.checkpoint,
            read_youtube=self.read_id,schedule_existing=self.arm,retry_seconds=())

    def test_full_stage_pending_restart_resume(self):
        with self.assertRaises(ProcessingPending): self.stage()
        self.assertEqual(self.ledger[MANIFESTS]["2026-11-21"]["phase"],"awaiting_processing")
        self.arm.assert_not_called()
        self.ready=True
        self.assertEqual(self.resume(self.worker()),self.ids)
        self.assertEqual(self.ledger[MANIFESTS]["2026-11-21"]["phase"],"both_scheduled")
        self.assertEqual(self.ledger[MANIFESTS]["2026-11-21"]["video_ids"],list(self.ids))
        self.assertEqual(self.upload.call_count,2)
        self.assertEqual(self.arm.call_count,2)
        self.assertEqual(self.ledger["days"],{})
        self.assertEqual(self.ledger["publications"],[])

    def test_two_leases_after_load_one_stale_no_effect(self):
        with self.assertRaises(ProcessingPending): self.stage()
        a,b=self.worker(),self.worker()
        a.load("run1");b.load("run1")
        intent=dict(reservation="run1",slot=1,video_id=self.ids[0],publish_at=self.window.publish_at)
        a.checkpoint("schedule_requested",**intent)
        with self.assertRaises(ScheduleHold): b.checkpoint("schedule_requested",**intent)
        with self.assertRaises(ScheduleHold): self.worker().load("run1")
        self.arm.assert_not_called()

    def test_partial_arm_does_not_allow_resume_or_new_claim(self):
        with self.assertRaises(ProcessingPending): self.stage()
        self.ready=True
        def arm(vid,when):
            if vid==self.ids[1]: raise TimeoutError
            self.arm_fn(vid,when)
        self.arm.side_effect=arm
        with self.assertRaises(TimeoutError): self.resume(self.worker())
        with self.assertRaises(ScheduleHold): self.resume(self.worker())
        with self.assertRaises((slots.SlotHold,ScheduleHold)): self.worker("another").reserve(self.window,self.jobs)
        self.assertEqual(self.arm.call_count,2)
        self.assertEqual(self.upload.call_count,2)
        self.assertEqual(self.ledger[MANIFESTS]["2026-11-21"]["phase"],"awaiting_processing")

    def test_upload_result_unknown_rejects_reclaim_and_resume(self):
        self.upload.side_effect=[TimeoutError()]
        with self.assertRaises(TimeoutError): self.stage()
        with self.assertRaises(ScheduleHold): self.worker().load("run1")
        with self.assertRaises((slots.SlotHold,ScheduleHold)): self.worker("another").reserve(self.window,self.jobs)
        self.upload.assert_called_once()
        self.arm.assert_not_called()

    def test_invalid_defense_manifest_ids_and_wrong_owner(self):
        with self.assertRaises(ProcessingPending): self.stage()
        with self.assertRaises(ScheduleHold): self.worker("other").load("run1")
        self.ledger[MANIFESTS]["2026-11-21"]["video_ids"].reverse()
        self.rev+=1
        with self.assertRaises(ScheduleHold): self.worker().load("run1")
        self.arm.assert_not_called()
