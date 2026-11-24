"""Offline adapter logic. No transport, credential storage, CLI, or upload.

Contract: http(method, resource, query, body) returns a decoded successful
response, rejects redirects/non-success, and is bound to ONE authorized
YouTube owner credential throughout. It NEVER retries PUT and bounds its
deadline to well inside the release cutoff. Resource is 'channels'/'videos'.

load_provenance(video_id) reads the *current* authorized protected ledger,
not a user-editable manifest or a claim derived from YouTube privacy alone.
Its PrivateUpload must originate in a durable private/unscheduled creation
receipt and unbroken controlled history excluding prior public availability.
Before schedule, authorize_write(receipt, publish_at_utc) must return True
only for the caller's exact latest CAS schedule intent, with an exclusive
writer across managed paths; the adapter cannot lock YouTube Studio operators.
Unknown external history, concurrent Studio writes, and unverifiable
transport settings are rollout blockers. YouTube status readback is not CAS.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from ojirap_morning_schedule import CHANNEL_ID, VIDEO_ID, ReleaseWindow, ScheduleHold

_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")
_PROCESS = {"succeeded": "processed", "processing": "uploaded",
            "failed": "failed", "terminated": None}
_BOOL_FLAGS = ("embeddable", "publicStatsViewable",
               "selfDeclaredMadeForKids", "containsSyntheticMedia")
_LICENSES = ("youtube", "creativeCommon")


@dataclass(frozen=True)
class PrivateUpload:
    video_id: str
    channel_id: str
    reservation: str
    job_id: str
    publish_at: str
    initially_private_and_unscheduled: bool
    never_public: bool


def _scope_time(value):
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise ScheduleHold("Expected UTC RFC 3339 publishAt")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleHold("Invalid UTC publishAt") from None


def _single(response, expected_id):
    if not isinstance(response, dict) or response.get("nextPageToken"):
        raise ScheduleHold("Unexpected or incomplete owner API response")
    rows = response.get("items")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ScheduleHold("Expected a single exact owner API record")
    if rows[0].get("id") != expected_id:
        raise ScheduleHold("Owner channel or video ID mismatch")
    return rows[0]


def _flags(status):
    if not isinstance(status, dict):
        raise ScheduleHold("Missing YouTube status")
    values = {}
    for name in _BOOL_FLAGS:
        if name in status:
            if type(status[name]) is not bool:
                raise ScheduleHold("Unexpected writable-status boolean")
            values[name] = status[name]
    if "license" in status:
        if status["license"] not in _LICENSES:
            raise ScheduleHold("Unexpected writable-status license")
        values["license"] = status["license"]
    return values


class YouTubeOwnerAdapter:
    def __init__(self, *, window, reservation, clock, http,
                 load_provenance, authorize_write):
        if not isinstance(window, ReleaseWindow) or not isinstance(reservation, str) or not reservation:
            raise ScheduleHold("Exact release window and reservation required")
        self.window = window
        self.reservation = reservation
        self.clock = clock
        self.http = http
        self.load_provenance = load_provenance
        self.authorize_write = authorize_write

    def _receipt(self, video_id):
        if not isinstance(video_id, str) or VIDEO_ID.fullmatch(video_id) is None:
            raise ScheduleHold("Malformed exact YouTube ID")
        receipt = self.load_provenance(video_id)
        if (not isinstance(receipt, PrivateUpload) or receipt.video_id != video_id
            or receipt.channel_id != CHANNEL_ID or receipt.reservation != self.reservation
            or receipt.publish_at != self.window.publish_at
            or not isinstance(receipt.job_id, str) or not receipt.job_id
            or receipt.initially_private_and_unscheduled is not True or receipt.never_public is not True):
            raise ScheduleHold("Missing trusted private creation and never-public provenance")
        return receipt

    def _read(self, video_id):
        receipt = self._receipt(video_id)
        _single(self.http("GET", "channels", {"part": "id", "mine": "true",
                                             "maxResults": "2"}, None), CHANNEL_ID)
        row = _single(self.http("GET", "videos",
                                {"part": "snippet,status,processingDetails", "id": video_id},
                                None), video_id)
        snippet, status, processing = (row.get(name) for name in (
            "snippet", "status", "processingDetails"))
        if not isinstance(snippet, dict) or snippet.get("channelId") != CHANNEL_ID:
            raise ScheduleHold("Exact video is not on Today Ojirap")
        flags = _flags(status)
        state = processing.get("processingStatus") if isinstance(processing, dict) else None
        if not isinstance(state, str) or state not in _PROCESS:
            raise ScheduleHold("Missing or unrecognized owner-only processing state")
        if state in ("succeeded", "processing") and status.get("uploadStatus") != _PROCESS[state]:
            raise ScheduleHold("Upload and processing status disagree; read only after reconciliation")
        if state not in ("succeeded", "processing") or status.get("privacyStatus") != "private":
            raise ScheduleHold("Video is not a processable private asset")
        raw = status.get("publishAt")
        if "publishAt" in status and raw is None:
            raise ScheduleHold("Unexpected null publishAt")
        actual = None if raw is None else _scope_time(raw)
        if actual is not None and actual != _scope_time(self.window.publish_at):
            raise ScheduleHold("Existing publishAt differs; will not replace")
        return receipt, dict(id=video_id, channel_id=CHANNEL_ID, privacy="private",
                             ever_public=False, processing_status=state,
                             publish_at=None if actual is None else self.window.publish_at), flags

    def read_youtube(self, video_id):
        """Report only exact validated owner state for the scheduling core."""
        return self._read(video_id)[1]

    def schedule_existing(self, video_id, publish_at):
        """Write at most once, verify independently, and never trigger an upload."""
        self.window.require_open(self.clock())
        if publish_at != self.window.publish_at:
            raise ScheduleHold("Expected exact release slot; no date substitution")
        target = _scope_time(publish_at)
        receipt, found, flags = self._read(video_id)
        if found["processing_status"] != "succeeded":
            raise ScheduleHold("Exact private video still processing")
        # An exact prior arm never earns a second PUT, regardless of why it exists.
        if found["publish_at"] == publish_at:
            return found
        if self.authorize_write(receipt, publish_at) is not True:
            raise ScheduleHold("Missing exclusive current durable scheduling intent")
        now = self.clock()
        self.window.require_open(now)
        if target <= now.astimezone(timezone.utc):
            raise ScheduleHold("Past publishAt would publish immediately")
        try:
            response = self.http("PUT", "videos", {"part": "status"},
                                 {"id": video_id, "status": {**flags,
                                     "privacyStatus": "private", "publishAt": publish_at}})
        except Exception:
            raise ScheduleHold("Scheduling write unconfirmed; reconcile exact ID without retry") from None
        # Do not rely on a successful PUT response alone.
        if not isinstance(response, dict) or response.get("id") != video_id:
            raise ScheduleHold("Unrecognized write receipt; reconcile exact ID without retry")
        _, actual, retained = self._read(video_id)
        if (actual["publish_at"] != publish_at or actual["processing_status"] != "succeeded"
            or any(retained.get(k) != v for k, v in flags.items())):
            raise ScheduleHold("Schedule or writable-status readback differs; reconcile without retry")
        return actual
