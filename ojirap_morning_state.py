"""Bind the 07:00 core to the same CAS slots and journal; no provider calls."""
from copy import deepcopy
from dataclasses import replace

import ojirap_slot_cas as slots
from ojirap_morning_schedule import CHANNEL_ID, ScheduleHold

MANIFESTS = "ojirap_morning_manifests_v1"
TRANSITIONS = {
    "upload_requested": "claimed",
    "private_id": "upload_requested",
    "schedule_requested": "private_id",
    "schedule_verified": "schedule_requested",
}


class MorningState:
    """One worker instance per operation, with a caller-stable owner reference."""

    def __init__(self, ledger, window, owner, *, clock):
        self.ledger, self.window, self.owner, self.clock = ledger, window, owner, clock
        self.day = window.day.isoformat()
        self.lease = None

    def _open(self, day):
        if day != self.day:
            return False
        self.window.require_open(self.clock())
        return True

    def reserve(self, window, jobs):
        if self.lease is not None or window != self.window:
            raise ScheduleHold("Reservation already loaded or window differs")
        jobs = tuple(jobs)
        if (len(jobs) != 2 or any(j.channel_id != CHANNEL_ID
                or j.editorial_passed is not True or j.rights_passed is not True for j in jobs)):
            raise ScheduleHold("Two approved Ojirap jobs required")
        def unstarted_read():
            ledger, rev = self.ledger.read()
            manifests = ledger.get(MANIFESTS, {})
            if not isinstance(manifests, dict) or self.day in manifests:
                raise ScheduleHold("Existing day manifest; reconcile, do not claim again")
            return ledger, rev

        self.lease = slots.claim(unstarted_read, self.ledger.write, day=self.day,
            owner=self.owner, kind="auto", still_open=self._open,
            items=tuple(slots.Item(j.job_id,j.topic_key,j.file_sha256) for j in jobs))
        return self.owner

    def _snapshot(self):
        lease, rows = slots.load(self.ledger.read, day=self.day, owner=self.owner)
        ledger, rev = self.ledger.read()
        if rev != lease.revision or lease.slots != ("1", "2") or any(r["kind"] != "auto" for r in rows.values()):
            raise ScheduleHold("Inconsistent two-slot journal snapshot")
        manifests = ledger.get(MANIFESTS, {})
        if not isinstance(manifests, dict):
            raise ScheduleHold("Malformed scheduling manifests")
        manifest = manifests.get(self.day)
        if manifest is not None and not isinstance(manifest, dict):
            raise ScheduleHold("Malformed scheduling manifest")
        return lease, rows, ledger, manifest

    def _manifest(self, rows, phase):
        return dict(phase=phase,reservation=self.owner,day=self.day,channel_id=CHANNEL_ID,
                    job_ids=[rows[k]["job"] for k in ("1","2")],
                    media_hashes=[rows[k]["media"] for k in ("1","2")],
                    video_ids=[rows[k]["video_id"] for k in ("1","2")],
                    publish_at=self.window.publish_at)

    def load(self, reservation):
        self.window.require_open(self.clock())
        if reservation != self.owner:
            raise ScheduleHold("Wrong owner reference")
        lease, rows, _, manifest = self._snapshot()
        expected = self._manifest(rows,"awaiting_processing")
        if manifest != expected or any(r["phase"] != "private_id" for r in rows.values()):
            raise ScheduleHold("No safe processing resume; reconcile existing effects")
        self.lease = lease
        return deepcopy(manifest)

    def checkpoint(self, event, *, reservation, **data):
        if reservation != self.owner or self.lease is None:
            raise ScheduleHold("Reservation was not acquired or loaded by worker")
        lease, rows, ledger, manifest = self._snapshot()
        if lease != self.lease:
            raise ScheduleHold("Stale worker revision; no request is authorized")

        prior_manifest = self._manifest(rows,"awaiting_processing")
        if event in TRANSITIONS:
            slot = data.get("slot")
            if type(slot) is not int or slot not in (1,2):
                raise ScheduleHold("Invalid slot")
            key=str(slot)
            row=rows[key]
            if event == "upload_requested":
                if set(data) != {"slot","hash"} or manifest is not None or data["hash"] != row["media"]:
                    raise ScheduleHold("Upload intent differs from bound job")
            elif event == "private_id":
                if set(data) != {"slot","video_id"} or manifest is not None:
                    raise ScheduleHold("Private receipt differs from staging phase")
            else:
                keys = {"slot","video_id","publish_at"} if event=="schedule_requested" else {"slot","video_id"}
                if (set(data) != keys or manifest != prior_manifest or data["video_id"] != row["video_id"]
                        or ("publish_at" in data and data["publish_at"] != self.window.publish_at)):
                    raise ScheduleHold("Schedule transition differs from exact durable manifest")
                other = rows["2" if slot == 1 else "1"]["phase"]
                if other != ("private_id" if slot == 1 else "schedule_verified"):
                    raise ScheduleHold("Unexpected peer phase; reconcile existing effects")
            self.lease=slots.advance(self.ledger.read,self.ledger.write,lease,key,
                expected=TRANSITIONS[event],phase=event,video_id=data.get("video_id"),still_open=self._open)
            return

        if event=="awaiting_processing":
            fields={k:v for k,v in prior_manifest.items() if k not in ("phase","reservation")}
            for key in ("job_ids","media_hashes","video_ids"):
                if isinstance(data.get(key),tuple):
                    data[key]=list(data[key])
            if data!=fields or manifest is not None or any(r["phase"]!="private_id" for r in rows.values()):
                raise ScheduleHold("Incomplete or inconsistent private pair")
            target=prior_manifest
        elif event=="both_scheduled":
            if (set(data)!={"video_ids","publish_at"} or data["video_ids"]!=tuple(prior_manifest["video_ids"])
                    or data["publish_at"]!=self.window.publish_at or manifest!=prior_manifest
                    or any(r["phase"]!="schedule_verified" for r in rows.values())):
                raise ScheduleHold("Cannot record a partial or mismatched pair")
            target=self._manifest(rows,"both_scheduled")
        else:
            raise ScheduleHold("Unsupported scheduling event")
        updated=deepcopy(ledger)
        updated.setdefault(MANIFESTS,{})[self.day]=target
        new=self.ledger.write(updated,lease.revision)
        if new is None or new==lease.revision:
            raise ScheduleHold("Manifest revision unconfirmed; reconcile")
        self.lease=replace(lease,revision=new)
