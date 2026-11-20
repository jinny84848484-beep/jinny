from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call

import ojirap_morning_schedule as s

class TestMorningSchedule(unittest.TestCase):
    def setUp(self):
        self.win = s.ReleaseWindow(date(2026,11,21))
        self.jobs = tuple(s.Job(str(i), str(i), str(i)*64, s.CHANNEL_ID, True, True) for i in (1,2))
        self.ids = ('a'*11, 'b'*11)
        self.readback = {}
        self.clock = Mock(return_value=self.win.start)
        self.reserve = Mock(return_value='reservation')
        self.checkpoint = Mock()
        self.upload = Mock(side_effect=self.upload_fn)
        self.read = Mock(side_effect=self.read_fn)
        self.arm = Mock(side_effect=self.arm_fn)

    def upload_fn(self, j):
        video_id=self.ids[int(j.job_id)-1]
        self.readback[video_id] = None
        return video_id

    def read_fn(self, video_id):
        return dict(id=video_id, channel_id=s.CHANNEL_ID, privacy='private',
                    processed=True, ever_public=False, publish_at=self.readback[video_id])

    def arm_fn(self, video_id, when):
        self.readback[video_id] = when

    def run_schedule(self):
        return s.stage_and_schedule(self.win, self.jobs, clock=self.clock, reserve=self.reserve,
                checkpoint=self.checkpoint, upload_private=self.upload,
                read_youtube=self.read, schedule_existing=self.arm)

    def test_fixed_kst_window_and_utc_previous_day(self):
        self.assertEqual(self.win.publish_at, "2026-11-20T22:00:00Z")
        self.assertEqual(self.run_schedule(), self.ids)
        self.assertEqual(self.arm.call_args_list,[call(i,self.win.publish_at) for i in self.ids])
        self.assertEqual(self.checkpoint.call_args.args[0], 'both_scheduled')

    def test_cutoff_is_exclusive_and_prior_day_is_not_today(self):
        for now in (self.win.cutoff, self.win.target, self.win.start-timedelta(days=1),
                    datetime(2026,11,21,5,7)):
            self.clock.return_value=now
            with self.assertRaises(s.ScheduleHold): self.run_schedule()
        self.reserve.assert_not_called()
        self.upload.assert_not_called()
        self.arm.assert_not_called()

    def test_slot_conflict_refuses_writes(self):
        self.reserve.return_value=None
        with self.assertRaises(s.ScheduleHold): self.run_schedule()
        self.upload.assert_not_called()
        self.arm.assert_not_called()

    def test_single_story_and_failed_rights_cannot_reserve(self):
        self.jobs=self.jobs[:1]
        with self.assertRaises(s.ScheduleHold): self.run_schedule()
        self.reserve.assert_not_called()

    def test_unknown_private_upload_is_never_retried_or_armed(self):
        self.upload.side_effect=[self.ids[0], TimeoutError()]
        with self.assertRaises(TimeoutError): self.run_schedule()
        self.assertEqual(self.upload.call_count, 2)
        self.assertEqual(self.checkpoint.call_args.args[0], 'upload_requested')
        self.arm.assert_not_called()

    def test_wrong_channel_readback_cannot_arm(self):
        self.read.side_effect=lambda v:{**self.read_fn(v),'channel_id':'wrong'}
        with self.assertRaises(s.ScheduleHold): self.run_schedule()
        self.arm.assert_not_called()

    def test_partial_schedule_not_retried_or_counted_complete(self):
        def partial(vid, when):
            if vid==self.ids[1]:raise TimeoutError
            self.arm_fn(vid,when)
        self.arm.side_effect=partial
        with self.assertRaises(TimeoutError): self.run_schedule()
        self.assertEqual(self.arm.call_count, 2)
        events=[c.args[0] for c in self.checkpoint.call_args_list]
        self.assertEqual(events.count('schedule_verified'),1)
        self.assertEqual(events[-1],'schedule_requested')
        self.assertNotIn('both_scheduled',events)

    def test_late_after_upload_does_not_schedule(self):
        def upload_last(j):
            vid=self.upload_fn(j)
            if j.job_id=='2':self.clock.return_value=self.win.cutoff
            return vid
        self.upload.side_effect=upload_last
        with self.assertRaises(s.ScheduleHold): self.run_schedule()
        self.arm.assert_not_called()

    def test_cas_error_before_submission_never_sends(self):
        self.checkpoint.side_effect=RuntimeError
        with self.assertRaises(RuntimeError): self.run_schedule()
        self.upload.assert_not_called()
        self.arm.assert_not_called()
