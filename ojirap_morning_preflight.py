"""Local-only 07:00 package preflight. Cannot upload, reserve, or schedule."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

import ojirap_slot_cas as slots
from ojirap_morning_schedule import CHANNEL_ID, ReleaseWindow, ScheduleHold
from ojirap_morning_state import MANIFESTS

MAX_JSON = 8 << 20
MAX_VIDEO = 512 << 20


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ScheduleHold("Duplicate JSON key")
        out[key] = value
    return out


def _reject_constant(_):
    raise ScheduleHold("Invalid JSON constant")


def _parse(raw):
    try:
        result=json.loads(raw.decode("utf-8"),object_pairs_hook=_object,parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ScheduleHold("Invalid UTF-8 JSON") from None
    if not isinstance(result,dict):
        raise ScheduleHold("Expected JSON object")
    return result


def _safe_open(dfd, name, maximum):
    if not hasattr(os,"O_NOFOLLOW") or not hasattr(os,"O_NONBLOCK"):
        raise ScheduleHold("Local no-symlink file safeguards unavailable")
    try:
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
        f=os.fdopen(fd,"rb")
    except OSError:
        raise ScheduleHold("Cannot open exact local regular file") from None
    st=os.fstat(f.fileno())
    if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=maximum:
        f.close()
        raise ScheduleHold("Invalid local regular-file size")
    return f


def _read(dfd,name):
    with _safe_open(dfd,name,MAX_JSON) as f:
        raw=f.read(MAX_JSON+1)
    if not 0<len(raw)<=MAX_JSON:
        raise ScheduleHold("JSON size bound exceeded")
    return raw


def _hash_file(dfd,name):
    with _safe_open(dfd,name,MAX_VIDEO) as f:
        h=hashlib.sha256();length=0
        while data:=f.read(1<<20):
            length+=len(data)
            if length>MAX_VIDEO:
                raise ScheduleHold("Video size bound exceeded")
            h.update(data)
    if length==0:
        raise ScheduleHold("Empty video")
    return h.hexdigest()


def _at(value):
    try:
        out=datetime.fromisoformat(value.replace("Z","+00:00"))
        if out.utcoffset() is not None:
            return out
    except (AttributeError, ValueError):
        pass
    raise ScheduleHold("Invalid reviewed_at")


def preflight(package, window, *, clock, approved_manifest_sha256=None, read_journal=None):
    """No writers or network; return separate review-pin and journal checks.

    SHA pin must come from an independent approved review channel; using a
    hash computed from an untrusted package is not review authentication.
    Journal callback only reads and returns (snapshot, revision). The output
    is point-in-time evidence, never a send authorization or slot lease.
    """
    now=clock()
    window.require_open(now)
    try:
        dfd=os.open(package,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    except (OSError,AttributeError):
        raise ScheduleHold("Cannot open exact package directory") from None
    try:
        raw=_read(dfd,"manifest.json")
        digest=hashlib.sha256(raw).hexdigest()
        pinned=approved_manifest_sha256 is not None
        if pinned and (not isinstance(approved_manifest_sha256,str) or not slots.HASH.fullmatch(approved_manifest_sha256) or not hmac.compare_digest(approved_manifest_sha256,digest)):
            raise ScheduleHold("Manifest does not match independent review pin")
        m=_parse(raw)
        if (set(m)!={"version","day","channel_id","publish_at","reviewed_at","jobs"}
            or type(m["version"]) is not int or m["version"]!=1
            or (m["day"],m["channel_id"],m["publish_at"])!=(window.day.isoformat(),CHANNEL_ID,window.publish_at)):
            raise ScheduleHold("Exact day, channel and publishAt manifest required")
        age=now-_at(m["reviewed_at"])
        if not timedelta(0)<=age<=timedelta(hours=36):
            raise ScheduleHold("Review date is future or stale")
        jobs=m["jobs"]
        if not isinstance(jobs,list) or len(jobs)!=2:
            raise ScheduleHold("Exactly two jobs required")
        items=[]
        for index,j in enumerate(jobs,1):
            if (not isinstance(j,dict) or set(j)!={"slot","job_id","topic_key","file","sha256",
                "channel_id","editorial_passed","rights_passed","audio_qa_passed"}
                or type(j["slot"]) is not int or j["slot"]!=index or j["channel_id"]!=CHANNEL_ID
                or j["file"]!=f"slot-{index}.mp4" or any(j[k] is not True for k in
                     ("editorial_passed","rights_passed","audio_qa_passed"))):
                raise ScheduleHold("Job scope, filename or review decision invalid")
            item=slots.Item(j["job_id"],j["topic_key"],j["sha256"])
            if (not slots._item_ok(item) or not item.job.startswith(m["day"]+"-")
                or not hmac.compare_digest(j["sha256"],_hash_file(dfd,j["file"]))):
                raise ScheduleHold("Bound local media/hash or job date mismatched")
            items.append(item)
        if any(len({getattr(i,k) for i in items})!=2 for k in ("job","topic","media")):
            raise ScheduleHold("Two distinct job, topic and media identities required")
    finally:
        os.close(dfd)
    window.require_open(clock())

    journal_checked=read_journal is not None
    if journal_checked:
        ledger,rev=read_journal()
        if not isinstance(ledger,dict):
            raise ScheduleHold("Invalid journal snapshot")
        manifests=ledger.get(MANIFESTS,{})
        if not isinstance(manifests,dict) or m["day"] in manifests:
            raise ScheduleHold("Existing morning state requires reconciliation")
        # In-memory evaluation only. Same shared slot policy as live claim,
        # but no caller-supplied write capability can be invoked.
        slots.claim(lambda:(ledger,rev),lambda *_:object(),day=m["day"],
                    owner="readiness-only",kind="auto",items=items,max_conflicts=0,
                    still_open=lambda _:window.require_open(clock()) is None)
    window.require_open(clock())
    return {"local_files_match":True,"review_pin_matched":pinned,
            "journal_snapshot_checked":journal_checked,"ready_for_dry_run":pinned and journal_checked,
            "live_ready":False,"mutation_performed":False,
            "day":m["day"],"channel_id":CHANNEL_ID,"publish_at":window.publish_at}


def main(argv=None):
    from datetime import date
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("package",type=Path)
    p.add_argument("--day",required=True,type=date.fromisoformat)
    p.add_argument("--approved-manifest-sha256")
    p.add_argument("--local-journal",type=Path)
    args=p.parse_args(argv)
    def local_snapshot():
        # Simulation only: local snapshot digest is not the GitHub blob SHA.
        parent_fd=os.open(args.local_journal.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            raw=_read(parent_fd,args.local_journal.name)
        finally:
            os.close(parent_fd)
        return _parse(raw),hashlib.sha256(raw).hexdigest()
    try:
        result=preflight(args.package,ReleaseWindow(args.day),clock=lambda:datetime.now(timezone.utc),
                     approved_manifest_sha256=args.approved_manifest_sha256,
                     read_journal=local_snapshot if args.local_journal else None)
    except (ScheduleHold, slots.SlotHold, OSError):
        print(json.dumps({"ready_for_dry_run":False,"live_ready":False,"status":"held"}))
        return 2
    print(json.dumps(result))
    return 0 if result["ready_for_dry_run"] else 2


if __name__=="__main__":
    raise SystemExit(main())
