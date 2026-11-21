"""Dedicated cooking release ledger; the persistence adapter is supplied by the operator.

open_snapshot() -> (document, opaque_revision). compare_and_set(document, revision)
must be atomic and never auto-retry; failed or ambiguous writes require readback.
Store this ledger privately and durably, never in a GitHub cache or rotating
run artifact. Every managed uploader must use the same ledger.
"""
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import timezone
from secrets import token_urlsafe
import re

from .publication import CHANNEL_ID, CheckedRelease, PublicationHold, _window

_VERSION = 1
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_LEASE = re.compile(r"[A-Za-z0-9_-]{16,96}\Z")
_CHAIN = {
    "reserved": "upload_requested",
    "upload_requested": "private_id",
    "private_id": "schedule_requested",
    "schedule_requested": "schedule_verified",
}
_RELEASE_FIELDS = {x.name for x in fields(CheckedRelease)}


def _valid(rx, value):
    return isinstance(value, str) and rx.fullmatch(value) is not None


def _release(document):
    if not isinstance(document, dict) or set(document) != _RELEASE_FIELDS:
        raise PublicationHold("Malformed exact kitchen release")
    release = CheckedRelease(**document)
    _, _, target = _window(release.day)
    if (release.channel_id != CHANNEL_ID
        or release.publish_at != target.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        or not isinstance(release.first_review_id, str) or not release.first_review_id.strip()
        or not isinstance(release.final_review_id, str) or not release.final_review_id.strip()
        or release.first_review_id == release.final_review_id
        or not all(_valid(_SHA, x) for x in (
            release.recipe_sha256, release.media_sha256, release.metadata_sha256))):
        raise PublicationHold("Wrong date/channel/reviews in kitchen release")
    return release


class CookingJournal:
    """Single operation context. A new process may resume only a known private ID."""

    def __init__(self, open_snapshot, compare_and_set, *, new_lease=None, approval_is_current=None):
        self._read = open_snapshot
        self._cas = compare_and_set
        self._new_id = new_lease or (lambda: token_urlsafe(24))
        self._current = approval_is_current
        self._owned = {}
        self._consumed = set()

    def _snapshot(self):
        doc, rev = self._read()
        if (not isinstance(rev, str) or not rev
            or not isinstance(doc, dict) or set(doc) != {"version", "channel_id", "days"}
            or type(doc["version"]) is not int or doc["version"] != _VERSION
            or doc["channel_id"] != CHANNEL_ID or not isinstance(doc["days"], dict)):
            raise PublicationHold("Need exact dedicated durable journal")
        leases, videos, hashes = set(), set(), set()
        for day, row in doc["days"].items():
            if not isinstance(row, dict) or set(row) != {"lease", "phase", "release", "video_id"}:
                raise PublicationHold("Corrupt kitchen journal row")
            release = _release(row["release"])
            key, phase, video = row["lease"], row["phase"], row["video_id"]
            if (release.day != day or not _valid(_LEASE, key) or key in leases
                or release.media_sha256 in hashes or not isinstance(phase, str)
                or phase not in (*_CHAIN, "schedule_verified")):
                raise PublicationHold("Inconsistent kitchen journal row")
            leases.add(key)
            hashes.add(release.media_sha256)
            if phase in ("reserved", "upload_requested"):
                if video is not None:
                    raise PublicationHold("Unexpected private video in unresolved upload")
            elif not _valid(_ID, video) or video in videos:
                raise PublicationHold("Missing or duplicate exact private ID")
            else:
                videos.add(video)
        return doc, rev

    def _persist(self, doc, rev, lease):
        # Revoke local send authority if compare-and-set fails or has an unclear outcome.
        self._owned.pop(lease, None)
        try:
            new_rev = self._cas(doc, rev)
        except Exception:
            raise PublicationHold("Kitchen ledger update unconfirmed; reconcile without replay") from None
        if not isinstance(new_rev, str) or not new_rev or new_rev == rev:
            raise PublicationHold("Kitchen ledger revision unconfirmed; reconcile without replay")
        self._owned[lease] = new_rev

    def reserve(self, checked):
        if not isinstance(checked, CheckedRelease):
            raise PublicationHold("Reviewed kitchen release required")
        payload = asdict(checked)
        _release(payload)
        doc, rev = self._snapshot()
        rows = doc["days"]
        lease = self._new_id()
        if (checked.day in rows or not _valid(_LEASE, lease)
            or any(r["lease"] == lease or r["release"]["media_sha256"] == checked.media_sha256
                   for r in rows.values())):
            raise PublicationHold("Kitchen date or video content already reserved")
        updated = deepcopy(doc)
        updated["days"][checked.day] = {"lease": lease, "phase": "reserved",
                                        "release": payload, "video_id": None}
        self._persist(updated, rev, lease)
        return lease

    def checkpoint(self, lease, event, *, release, video_id=None):
        if not _valid(_LEASE, lease) or lease not in self._owned or not isinstance(release, CheckedRelease):
            raise PublicationHold("No current lease authority")
        doc, rev = self._snapshot()
        row = doc["days"].get(release.day)
        if (rev != self._owned[lease] or row is None or row["lease"] != lease
            or _release(row["release"]) != release or _CHAIN.get(row["phase"]) != event):
            self._owned.pop(lease, None)
            raise PublicationHold("Stale lease or wrong phase; reconcile without replay")
        if event == "upload_requested":
            valid_id = video_id is None
        elif event == "private_id":
            valid_id = _valid(_ID, video_id) and not any(
                other["video_id"] == video_id for other in doc["days"].values())
        else:
            valid_id = _valid(_ID, video_id) and row["video_id"] == video_id
        if not valid_id:
            raise PublicationHold("Wrong private receipt for kitchen lease")
        updated = deepcopy(doc)
        updated["days"][release.day].update(phase=event, video_id=video_id)
        self._persist(updated, rev, lease)

    def load_pending(self, lease):
        if not _valid(_LEASE, lease):
            raise PublicationHold("Unknown kitchen lease")
        doc, rev = self._snapshot()
        row = next((row for row in doc["days"].values() if row["lease"] == lease), None)
        if row is None or row["phase"] != "private_id":
            raise PublicationHold("No resumable private receipt; never replay an intent")
        release = _release(row["release"])
        if not callable(self._current) or self._current(release) is not True:
            raise PublicationHold("Approval is not known to be current")
        self._owned[lease] = rev
        return {**row, "release": release}


    def authorize_effect(self, release, phase, *, video_id=None):
        """Consume a current, single process-local send authority; never replay."""
        if phase not in ("upload_requested", "schedule_requested") or not isinstance(release, CheckedRelease):
            raise PublicationHold("Unknown kitchen transport intent")
        if not callable(self._current) or self._current(release) is not True:
            raise PublicationHold("Publication approval expired or unavailable")
        doc, rev = self._snapshot()
        row = doc["days"].get(release.day)
        if (row is None or not _valid(_LEASE, row["lease"])
            or self._owned.get(row["lease"]) != rev
            or row["phase"] != phase or row["video_id"] != video_id
            or _release(row["release"]) != release):
            raise PublicationHold("No current durable transport intent")
        token = (row["lease"], phase)
        if token in self._consumed:
            raise PublicationHold("Transport intent already consumed; reconcile without replay")
        self._consumed.add(token)


class JournaledReleaseAdapter:
    """Composition for the injectable release runner. Does not provide transport."""

    def __init__(self, journal, *, check_clear, upload_private, read, schedule_private):
        self._journal = journal
        self.check_clear = check_clear
        self.read = read
        self._upload = upload_private
        self._schedule = schedule_private

    def reserve(self, release):
        return self._journal.reserve(release)

    def checkpoint(self, lease, event, **state):
        return self._journal.checkpoint(lease, event, **state)

    def load_pending(self, lease):
        return self._journal.load_pending(lease)

    def upload_private(self, release, media, metadata):
        self._journal.authorize_effect(release, "upload_requested")
        return self._upload(release, media, metadata)

    def schedule_private(self, release, video_id):
        self._journal.authorize_effect(release, "schedule_requested", video_id=video_id)
        return self._schedule(release, video_id)
