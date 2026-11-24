"""Two managed Ojirap base slots; no network/credential code.

Backend contracts: read() returns (fresh ledger dict, opaque non-None revision);
write(ledger, expected_revision) returns new revision or raises KnownConflict
only for a verified no-write CAS rejection. All other errors propagate. It
must atomically compare-and-write the *same* ledger used by every writer.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import re

CHANNEL = "UCZj1FlWDcoiEQNLfpwc5qtw"
ROOT = "ojirap_base_slots_v1"
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


class SlotHold(RuntimeError):
    """No new external write is permitted."""


class KnownConflict(SlotHold):
    """Backend verified the CAS did not commit."""


TRANSITIONS = {
    "claimed": {"upload_requested", "publish_requested"},
    "upload_requested": {"private_id"},
    "private_id": {"schedule_requested", "publish_requested"},
    "schedule_requested": {"schedule_verified"},
    "schedule_verified": {"public_verified"},
    "publish_requested": {"public_verified"},
    "public_verified": set(),
}
INTENT = {"upload_requested", "schedule_requested", "publish_requested"}
LINKED = {"private_id", "schedule_requested", "schedule_verified", "public_verified"}


@dataclass(frozen=True)
class Item:
    job: str
    topic: str
    media: str


@dataclass(frozen=True)
class Lease:
    day: str
    owner: str
    slots: tuple
    revision: object


def _match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _item_ok(x):
    return isinstance(x, Item) and _match(TOKEN, x.job) and _match(TOKEN, x.topic) and _match(HASH, x.media)


def _day_ok(s):
    return isinstance(s, str) and date.fromisoformat(s).isoformat() == s


def _require_day(s):
    try:
        valid = _day_ok(s)
    except ValueError:
        valid = False
    if not valid:
        raise SlotHold("Need canonical KST day")


def _read(read, day):
    ledger, rev = read()
    if rev is None or not isinstance(ledger, dict):
        raise SlotHold("Ledger or durable revision unavailable")
    pubs = ledger.get("publications")
    days = ledger.get("days")
    slots = ledger.get(ROOT, {})
    if not isinstance(slots, dict) or not isinstance(days, dict) or not isinstance(pubs, list) or any(not isinstance(r, dict) for r in pubs):
        raise SlotHold("Malformed ledger")
    for key, group in slots.items():
        _require_day(key)
        if not isinstance(group, dict) or any(k not in {"1","2"} for k in group):
            raise SlotHold("Malformed slot group")
        for row in group.values():
            if not isinstance(row, dict) or row.get("channel_id") != CHANNEL or row.get("kind") not in ("manual", "auto") or not _match(TOKEN, row.get("owner")):
                raise SlotHold("Malformed slot binding")
            phase = row.get("phase")
            vid = row.get("video_id")
            if not isinstance(phase, str) or phase not in TRANSITIONS or not _item_ok(Item(row.get("job"),row.get("topic"),row.get("media"))):
                raise SlotHold("Malformed slot state")
            if (vid is not None and not _match(VIDEO,vid)) or (phase in LINKED and vid is None):
                raise SlotHold("Malformed video identity")
        local = tuple(group.values())
        for k in ("job","topic","media","video_id"):
            values = [r[k] for r in local if r.get(k) is not None]
            if len(set(values)) != len(values):
                raise SlotHold("Duplicate base slot binding")
        autos = [r for r in local if r["kind"] == "auto"]
        if autos and (len(autos) != 2 or autos[0]["owner"] != autos[1]["owner"]):
            raise SlotHold("Incomplete automatic pair")
    return ledger, rev, slots.get(day, {})


def _publication_is_same_day(r, day):
    # Explicit records are preferred, but historical Ojirap job IDs and
    # aware at/verified_at timestamps are recognized without assuming UTC day.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if r.get("channel") != "ojirap" and r.get("channel_id") != CHANNEL:
        return False
    if r.get("day") == day or str(r.get("job_id","")).startswith(day + "-"):
        return True
    for name in ("at","verified_at"):
        try:
            at = datetime.fromisoformat(str(r.get(name)).replace("Z","+00:00"))
            if at.utcoffset() is not None and at.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat() == day:
                return True
        except ValueError:
            pass
    return False


def _put(write, ledger, rev):
    result = write(ledger, rev)
    if result is None or result == rev:
        raise SlotHold("No new durable revision; reconcile, do not send")
    return result


def claim(read, write, *, day, owner, kind, items, still_open, max_conflicts=1):
    """Claim two auto or one/two manual slots in one CAS, before providers.

    still_open(day) pins the intended KST date/window; checked before each CAS.
    Retry only typed, proven no-write CAS conflict, with a fresh full read.
    Never restart from claim after an ambiguous write or external request.
    """
    _require_day(day)
    items = tuple(items)
    if not _match(TOKEN, owner) or kind not in ("manual","auto") or len(items) not in ((2,) if kind=="auto" else (1,2)) or not all(map(_item_ok,items)):
        raise SlotHold("Invalid slot request")
    if any(len({getattr(i,k) for i in items}) != len(items) for k in ("job","topic","media")):
        raise SlotHold("Distinct inputs required")
    if type(max_conflicts) is not int or not 0 <= max_conflicts <= 2:
        raise SlotHold("Unbounded CAS retry")
    for attempt in range(max_conflicts + 1):
        if still_open(day) is not True:
            raise SlotHold("Wrong KST date or window closed")
        ledger, rev, group = _read(read, day)
        legacy = ledger["days"].get(day, {})
        if not isinstance(legacy, dict) or legacy.get("status") not in (None, "") or legacy.get("retry_hold") or legacy.get("checkpoints"):
            raise SlotHold("Existing day state needs reconciliation")
        if any(row["owner"] == owner for rows in ledger.get(ROOT, {}).values() for row in rows.values()):
            raise SlotHold("Owner reference already held; resume exact known reservation")
        bound = {row["video_id"] for row in group.values() if row["video_id"]}
        # Unknown/legacy same-day publication cannot be silently counted as new
        # capacity; explicit extra-only receipts are managed independently.
        if any(_publication_is_same_day(r, day) and r.get("video_id") not in bound and r.get("slot_kind") != "extra"
               for r in ledger["publications"]):
            raise SlotHold("Existing unmatched publication; reconcile first")
        if any(getattr(i,k) == r[k] for i in items for r in group.values() for k in ("job","topic","media")):
            raise SlotHold("Job, topic or media already reserved")
        free = tuple(k for k in ("1","2") if k not in group)[:len(items)]
        if len(free) != len(items):
            raise SlotHold("Insufficient base slots")
        copy = deepcopy(ledger)
        target=copy.setdefault(ROOT,{}).setdefault(day,{})
        for k,i in zip(free,items):
            target[k] = dict(channel_id=CHANNEL,kind=kind,owner=owner,job=i.job,topic=i.topic,
                             media=i.media,phase="claimed",video_id=None)
        if still_open(day) is not True:
            raise SlotHold("Window closed during acquisition")
        try:
            return Lease(day, owner, free, _put(write, copy, rev))
        except KnownConflict:
            if attempt == max_conflicts:
                raise
    raise AssertionError("unreachable")


def load(read, *, day, owner):
    """Read an exact managed lease after restart; does not grant send authority."""
    _require_day(day)
    if not _match(TOKEN,owner):
        raise SlotHold("Invalid owner reference")
    _,rev,group = _read(read,day)
    slots = tuple(k for k in ("1","2") if k in group and group[k]["owner"] == owner)
    if not slots:
        raise SlotHold("Reservation not found")
    return Lease(day,owner,slots,rev), {k:deepcopy(group[k]) for k in slots}


def advance(read, write, lease, slot, *, expected, phase, video_id=None, still_open=None):
    """Exactly one CAS. Passing an old revision or duplicate intent cannot write.

    A returned new lease on an intent authorizes at most that single managed
    attempt, not replay. For receipt transitions the caller first verifies
    the provider's exact owned ID, channel and resulting visibility.
    """
    if slot not in lease.slots or expected not in TRANSITIONS or phase not in TRANSITIONS[expected]:
        raise SlotHold("Wrong slot or phase transition")
    if phase in INTENT and (still_open is None or still_open(lease.day) is not True):
        raise SlotHold("Wrong KST date or window closed")
    ledger, rev, group = _read(read,lease.day)
    row = group.get(slot)
    owned = tuple(k for k in ("1","2") if k in group and group[k]["owner"] == lease.owner)
    if rev != lease.revision or owned != lease.slots or not row or row["owner"] != lease.owner or row["phase"] != expected:
        raise SlotHold("Stale lease or phase; reconcile, do not send")
    if phase in INTENT and any(
        _publication_is_same_day(p, lease.day) and p.get("slot_kind") != "extra"
        and p.get("video_id") not in {r["video_id"] for r in group.values() if r["video_id"]}
        for p in ledger["publications"]
    ):
        raise SlotHold("New unmatched publication; reconcile before sending")
    if phase == "publish_requested" and row["kind"] != "manual":
        raise SlotHold("Automatic release cannot publish immediately")
    if phase == "schedule_requested" and (row["kind"] != "auto" or any(
        group[k]["phase"] not in ("private_id","schedule_verified") for k in lease.slots
    )):
        raise SlotHold("Automatic pair not both staged, or an intent is unresolved")
    if video_id is not None:
        if not _match(VIDEO, video_id) or row.get("video_id") not in (None,video_id):
            raise SlotHold("Wrong video receipt")
        for day,rows in ledger.get(ROOT,{}).items():
            if any(other.get("video_id")==video_id and (day,k)!=(lease.day,slot) for k,other in rows.items()):
                raise SlotHold("Duplicate video receipt")
        if row.get("video_id") is None and any(p.get("video_id")==video_id for p in ledger["publications"]):
            raise SlotHold("Already recorded video receipt")
    if phase in LINKED and (video_id or row.get("video_id")) is None:
        raise SlotHold("Missing known video ID")
    copy=deepcopy(ledger)
    copy[ROOT][lease.day][slot].update(phase=phase,video_id=video_id or row.get("video_id"))
    if phase in INTENT and still_open(lease.day) is not True:
        raise SlotHold("Window closed during intent")
    return Lease(lease.day,lease.owner,lease.slots,_put(write,copy,rev))
