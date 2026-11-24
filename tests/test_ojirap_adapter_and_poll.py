from datetime import date, timedelta
import unittest
from unittest.mock import Mock

from ojirap_ledger_adapter import GitHubLedgerCAS, JournalWriteUnconfirmed
from ojirap_morning_schedule import CHANNEL_ID, Job, ReleaseWindow, ScheduleHold
from ojirap_readiness_poll import poll_resume
from ojirap_slot_cas import KnownConflict, SlotHold

OLD, NEW = 'a'*40, 'b'*40

class AdapterTests(unittest.TestCase):
    def test_valid_write_carries_exact_blobs(self):
        get=Mock(return_value=({'days':{}},OLD));put=Mock(return_value=NEW)
        a=GitHubLedgerCAS(get,put)
        self.assertEqual(a.read(),({'days':{}},OLD))
        value={'days':{'test':{}}}
        self.assertEqual(a.write(value,OLD),NEW)
        put.assert_called_once_with(value,OLD)

    def test_only_fresh_before_write_mismatch_is_typed_safe_conflict(self):
        put=Mock()
        a=GitHubLedgerCAS(lambda: ({},NEW),put)
        with self.assertRaises(KnownConflict): a.write({},OLD)
        put.assert_not_called()

    def test_missing_journal_not_created(self):
        put=Mock()
        a=GitHubLedgerCAS(lambda: ({},None),put)
        with self.assertRaises(SlotHold): a.write({},OLD)
        put.assert_not_called()

    def test_post_send_409_422_timeout_and_malformed_no_retry(self):
        for error in (RuntimeError('HTTP 409'),RuntimeError('HTTP 422'),TimeoutError(),KnownConflict()):
            put=Mock(side_effect=error)
            a=GitHubLedgerCAS(lambda: ({},OLD),put)
            with self.assertRaises(JournalWriteUnconfirmed): a.write({},OLD)
            self.assertEqual(put.call_count,1)
        for reply in (OLD,'wrong',None):
            put=Mock(return_value=reply)
            with self.assertRaises(JournalWriteUnconfirmed):
                GitHubLedgerCAS(lambda: ({},OLD),put).write({},OLD)

class PollTests(unittest.TestCase):
    def setUp(self):
        self.window=ReleaseWindow(date(2026,11,21))
        self.jobs=tuple(Job(str(i),str(i),str(i)*64,CHANNEL_ID,True,True) for i in (1,2))
        self.ids=('a'*11,'b'*11)
        self.state=dict(phase='awaiting_processing',reservation='r',day='2026-11-21',
            channel_id=CHANNEL_ID,job_ids=['1','2'],media_hashes=['1'*64,'2'*64],
            video_ids=self.ids,publish_at=self.window.publish_at)
        self.seconds=0; self.wall=self.window.start
        self.status='processing'
        self.times={}
        self.read=Mock(side_effect=self._read)
        self.checkpoint=Mock()
        self.arm=Mock(side_effect=self._arm)
        self.load=Mock(side_effect=lambda _:self.state.copy())
        self.sleep=Mock(side_effect=self._sleep)
    def _sleep(self,s): self.seconds+=s
    def _read(self,v):
        return dict(id=v,channel_id=CHANNEL_ID,privacy='private',ever_public=False,
                    processing_status=self.status,publish_at=self.times.get(v))
    def _arm(self,v,t): self.times[v]=t
    def run_poll(self,**kw):
        return poll_resume(self.window,self.jobs,'r',
            clock=lambda:self.wall+timedelta(seconds=self.seconds),load=self.load,
            checkpoint=self.checkpoint,read_youtube=self.read,schedule_existing=self.arm,
            sleep_fn=self.sleep,monotonic_fn=lambda:self.seconds,**kw)
    def test_pending_then_ready_once_with_exact_ids(self):
        def wait(s): self._sleep(s);self.status='succeeded'
        self.sleep.side_effect=wait
        self.assertEqual(self.run_poll(),self.ids)
        self.assertEqual(self.load.call_count,2)
        self.sleep.assert_called_once_with(15)
        self.assertEqual([x.args[0] for x in self.arm.call_args_list],list(self.ids))
    def test_pending_attempts_bounded(self):
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.assertEqual(self.read.call_count,8)
        self.assertEqual(self.seconds,105)
        self.arm.assert_not_called()
    def test_late_or_budget_does_not_wait_or_arm(self):
        with self.assertRaises(ScheduleHold): self.run_poll(budget_seconds=15)
        self.wall=self.window.cutoff-timedelta(seconds=10)
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.sleep.assert_not_called();self.arm.assert_not_called()
    def test_failed_or_existing_unresolved_intent_is_not_polled(self):
        self.status='failed'
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.state['phase']='schedule_requested'
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.sleep.assert_not_called();self.arm.assert_not_called()
    def test_pending_then_changed_manifest_is_hold(self):
        def wait(s): self._sleep(s);self.state['phase']='schedule_requested'
        self.sleep.side_effect=wait
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.sleep.assert_called_once_with(15);self.arm.assert_not_called()
    def test_arm_timeout_is_not_reattempted(self):
        self.status='succeeded'
        self.arm.side_effect=TimeoutError
        with self.assertRaises(TimeoutError): self.run_poll()
        self.sleep.assert_not_called();self.arm.assert_called_once()
    def test_clock_crossing_cutoff_after_checkpoint_never_sends(self):
        self.status='succeeded'
        self.checkpoint.side_effect=lambda *a,**kw:setattr(self,'wall',self.window.cutoff)
        with self.assertRaises(ScheduleHold): self.run_poll()
        self.arm.assert_not_called();self.sleep.assert_not_called()
