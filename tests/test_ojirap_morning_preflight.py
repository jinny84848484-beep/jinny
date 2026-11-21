from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ojirap_morning_schedule import CHANNEL_ID, ReleaseWindow, ScheduleHold
from ojirap_morning_preflight import preflight
from ojirap_morning_state import MANIFESTS
from ojirap_slot_cas import SlotHold

class PreflightTests(TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.p=Path(tmp.name)
        self.win=ReleaseWindow(date(2026,11,21));self.clock=lambda:self.win.start
        self.ledger={"days":{},"publications":[]}
        self.m=dict(version=1,day=self.win.day.isoformat(),channel_id=CHANNEL_ID,
                    publish_at=self.win.publish_at,reviewed_at=self.win.start.isoformat(),jobs=[])
        for i in [1,2]:
            raw=b"synthetic bytes "+str(i).encode(); name=f"slot-{i}.mp4"
            (self.p/name).write_bytes(raw)
            self.m["jobs"].append(dict(slot=i,job_id=f"{self.m['day']}-job-{i}",
                topic_key=f"topic-{i}",file=name,sha256=hashlib.sha256(raw).hexdigest(),
                channel_id=CHANNEL_ID,editorial_passed=True,rights_passed=True,audio_qa_passed=True))
        self.save()
    def save(self):
        raw=json.dumps(self.m).encode()
        (self.p/"manifest.json").write_bytes(raw)
        self.digest=hashlib.sha256(raw).hexdigest()
    def check(self, **overrides):
        return preflight(self.p,self.win,**{**dict(clock=self.clock,approved_manifest_sha256=self.digest,
                            read_journal=lambda:(self.ledger,object())),**overrides})
    def test_exact_pin_media_two_jobs_and_journal_read_only(self):
        previous=deepcopy(self.ledger)
        got=self.check()
        self.assertTrue(got["ready_for_dry_run"])
        self.assertFalse(got["live_ready"])
        self.assertFalse(got["mutation_performed"])
        self.assertEqual(previous,self.ledger)
    def test_self_report_without_pin_is_untrusted_and_no_journal_not_ready(self):
        self.assertFalse(self.check(approved_manifest_sha256=None)["ready_for_dry_run"])
        self.assertFalse(self.check(read_journal=None)["ready_for_dry_run"])
    def test_changed_manifest_or_media_rejected(self):
        old=self.digest
        self.m["jobs"][0]["topic_key"]="new-topic";self.save()
        with self.assertRaises(ScheduleHold):self.check(approved_manifest_sha256=old)
        (self.p/"slot-1.mp4").write_bytes(b"corrupted")
        with self.assertRaises(ScheduleHold):self.check()
    def test_exact_fields_channel_day_and_absolute_path_checks(self):
        orig=deepcopy(self.m)
        for change in (
            {"file":"/tmp/slot-1.mp4"},{"channel_id":"other"},{"slot":True},
            {"job_id":"2026-11-22-other"},{"audio_qa_passed":1},{"rights_passed":False},
            {"topic_key":"topic-2"}):
            self.m=deepcopy(orig); self.m["jobs"][0].update(change);self.save()
            with self.assertRaises(ScheduleHold):self.check()
        self.m=orig;self.m["publish_at"]="past";self.save()
        with self.assertRaises(ScheduleHold):self.check()
    def test_duplicate_json_and_symlink_rejected(self):
        (self.p/"manifest.json").write_text('{"version":1,"version":1}')
        with self.assertRaises(ScheduleHold):self.check(approved_manifest_sha256=None)
        self.save()
        (self.p/"slot-1.mp4").unlink()
        (self.p/"slot-1.mp4").symlink_to(self.p/"slot-2.mp4")
        with self.assertRaises(ScheduleHold):self.check()
    def test_unknown_state_existing_publication_and_missing_revision(self):
        day=self.m["day"]
        for journal in (
            {"days":{day:{"status":"upload_submitted"}},"publications":[]},
            {"days":{},"publications":[{"channel_id":CHANNEL_ID,"day":day}]},
            {"days":{},"publications":[] ,MANIFESTS:{day:{"phase":"awaiting_processing"}}}):
            with self.assertRaises((ScheduleHold,SlotHold)):
                self.check(read_journal=lambda:(journal,"snapshot"))
        with self.assertRaises(SlotHold): self.check(read_journal=lambda:(self.ledger,None))
    def test_cutoff_and_stale_or_future_review(self):
        with self.assertRaises(ScheduleHold):self.check(clock=lambda:self.win.cutoff)
        for t in (self.win.start+timedelta(seconds=1),self.win.start-timedelta(hours=37)):
            self.m["reviewed_at"]=t.isoformat();self.save()
            with self.assertRaises(ScheduleHold):self.check()
