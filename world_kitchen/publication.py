"""Offline-only reviewed publishing protocol. No credentials, HTTP or CLI.

All write callbacks must be bound to this channel, one shared durable CAS
journal across every non-Studio writer, and the exact approved render.
Studio operators must share this gate or pause manual writes. GitHub cache is
not this journal. Uncertain writes are reconciled read-only, never replayed.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

CHANNEL_ID = "UCkuz6ra5_hZ1MSAbFthqDcg"
KST = ZoneInfo("Asia/Seoul")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")


class PublicationHold(RuntimeError):
    """Stop, preserving journal intent for owner reconciliation."""


@dataclass(frozen=True)
class FirstReview:
    """From a trusted approval store after recipe/safety and media rights review."""
    review_id: str
    day: str
    channel_id: str
    recipe_sha256: str
    recipe_safe: bool
    rights_cleared: bool
    reviewed_at: datetime


@dataclass(frozen=True)
class FinalReview:
    """Exact output and external metadata explicitly approved for this release."""
    review_id: str
    first_review_id: str
    day: str
    channel_id: str
    recipe_sha256: str
    media_sha256: str
    metadata_sha256: str
    publish_at: str
    audiovisual_passed: bool
    captions_passed: bool
    approved_for_publication: bool
    reviewed_at: datetime


@dataclass(frozen=True)
class CheckedRelease:
    day: str
    channel_id: str
    publish_at: str
    first_review_id: str
    final_review_id: str
    recipe_sha256: str
    media_sha256: str
    metadata_sha256: str


def _aware(t):
    return isinstance(t, datetime) and t.utcoffset() is not None


def _window(day):
    try:
        civil = date.fromisoformat(day)
    except (ValueError, TypeError):
        raise PublicationHold("Need a canonical KST release date") from None
    if day != civil.isoformat():
        raise PublicationHold("Need a canonical KST release date")
    target = datetime.combine(civil, time(19), KST)
    return target - timedelta(hours=2), target - timedelta(minutes=20), target


def _require_open(day, now):
    start, cutoff, target = _window(day)
    if not _aware(now) or not start <= now < cutoff:
        raise PublicationHold("Only 17:00–18:40 KST local-day preparation is allowed")
    return target.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _id(value, regex):
    return isinstance(value, str) and regex.fullmatch(value) is not None


def metadata_digest(metadata):
    """Exactly one canonical metadata shape; no caller-supplied publish override."""
    if not isinstance(metadata, dict) or set(metadata) != {
            "title", "description", "made_for_kids", "contains_synthetic_media"}:
        raise PublicationHold("Need exact reviewed YouTube metadata without write overrides")
    if (not isinstance(metadata["title"], str) or not metadata["title"].strip()
        or len(metadata["title"]) > 100 or not isinstance(metadata["description"], str)
        or len(metadata["description"].encode("utf-8")) > 5000
        or type(metadata["made_for_kids"]) is not bool
        or type(metadata["contains_synthetic_media"]) is not bool):
        raise PublicationHold("Invalid title, description or setting")
    return sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")).hexdigest()


def preflight(day, *, now, recipe_sha256, rendered_at, media, metadata, first, final):
    """Pure check. For production, root approvals must come from a trusted store.

    recipe_sha256 is of the source checked by review, and the trusted renderer
    must attest it used that same source. The in-memory immutable media object
    is hashed and then passed unchanged to upload, avoiding path substitution.
    """
    expected_at = _require_open(day, now)
    if not _id(recipe_sha256, _SHA) or not _aware(rendered_at):
        raise PublicationHold("Missing trusted source/render lineage")
    if not isinstance(media, bytes) or not media:
        raise PublicationHold("Missing immutable rendered MP4")
    media_sha256 = sha256(media).hexdigest()
    meta_sha256 = metadata_digest(metadata)
    if (not isinstance(first, FirstReview) or not first.review_id
        or first.day != day or first.channel_id != CHANNEL_ID
        or first.recipe_sha256 != recipe_sha256 or first.recipe_safe is not True
        or first.rights_cleared is not True or not _aware(first.reviewed_at)
        or first.reviewed_at > rendered_at):
        raise PublicationHold("Recipe/safety/rights approval is absent or wrong")
    if (not isinstance(final, FinalReview) or not final.review_id or final.review_id == first.review_id
        or final.first_review_id != first.review_id or final.day != day
        or final.channel_id != CHANNEL_ID or final.recipe_sha256 != recipe_sha256
        or final.media_sha256 != media_sha256 or final.metadata_sha256 != meta_sha256
        or final.publish_at != expected_at or not _aware(final.reviewed_at)
        or not rendered_at <= final.reviewed_at <= now
        or final.audiovisual_passed is not True or final.captions_passed is not True
        or final.approved_for_publication is not True):
        raise PublicationHold("Final media, title/description/settings or 19:00 approval is absent or wrong")
    return CheckedRelease(day, CHANNEL_ID, expected_at, first.review_id, final.review_id,
                          recipe_sha256, media_sha256, meta_sha256)


def _verify(read, video_id, checked, *, publish_at):
    row = read(video_id)
    if (not isinstance(row, dict) or row.get("id") != video_id
        or row.get("channel_id") != CHANNEL_ID or row.get("privacy") != "private"
        or row.get("owner_verified") is not True or row.get("never_public") is not True
        or row.get("media_sha256") != checked.media_sha256
        or row.get("metadata_sha256") != checked.metadata_sha256
        or row.get("processing_status") not in ("processing", "succeeded")
        or "publish_at" not in row or row["publish_at"] != publish_at):
        raise PublicationHold("Exact private video/channel/processing/schedule readback mismatch")
    return row["processing_status"]


def prepare_day(queue, day, *, clock, load_reviewed, reserve, checkpoint,
                check_clear, upload_private, read, schedule_private):
    """Prepare an exact 19:00 schedule; not a direct publish or auto content generator.

    load_reviewed(entry) returns immutable bytes, metadata and review values for
    preflight. No trust in queue flags alone. Entry uniqueness is checked first.
    check_clear(checked): True only after authoritative channel/day duplicate scan.
    reserve(checked): one CAS claim, conflicts on any existing day/hash/upload;
       no retry after unknown outcome; no GitHub cache. Returns opaque nonempty ID.
    checkpoint(lease, event, ...): atomic compare-and-set current lease; raises
       on stale/unknown write, including collision with all other writers.
    upload_private(checked, media, metadata): exact owner-only private unscheduled
       upload, never retries, return exact ID; all transport deadlines bounded.
    read(id): normalize exact owner state; metadata_sha256 from actual readback,
       media_sha256 and never_public from protected private-upload evidence/CAS.
       YouTube API does not expose an uploaded-file hash or all past visibility.
    schedule_private(checked, id): exact ID owner-only status update only, with
       fresh private/readiness/prior-schedule checks, preserving status flags,
       never retries, never substitutes a time. No generic/public fallback.
    """
    _window(day)
    if not isinstance(queue, list) or any(not isinstance(e,dict) for e in queue):
        raise PublicationHold("Malformed cooking queue")
    entries = [e for e in queue if e.get("date") == day]
    if not entries:
        return {"status":"no_ready_episode","date":day,"scheduled":False}
    if len(entries) != 1:
        raise PublicationHold("Duplicate same-day cooking queue entries")
    if entries[0].get("reviewed") is not True:
        return {"status":"awaiting_recipe_and_rights_review","date":day,"scheduled":False}
    _require_open(day, clock())
    packet = load_reviewed(entries[0])
    if not isinstance(packet, dict):
        raise PublicationHold("No trusted rendered and reviewed package")
    meta = packet.get("metadata")
    media = packet.get("media")
    checked = preflight(day, now=clock(), recipe_sha256=packet.get("recipe_sha256"),
                        rendered_at=packet.get("rendered_at"), media=media, metadata=meta,
                        first=packet.get("first"), final=packet.get("final"))
    # All metadata values validated scalar. Freeze the approved serial form.
    meta = dict(meta)
    if metadata_digest(meta) != checked.metadata_sha256:
        raise PublicationHold("Approved metadata changed")
    if check_clear(checked) is not True:
        raise PublicationHold("Day/channel duplicate state unknown or already occupied")
    _require_open(day, clock())
    lease = reserve(checked)
    if not isinstance(lease,str) or not lease:
        raise PublicationHold("Cooking release journal unavailable or occupied")
    _require_open(day, clock())
    checkpoint(lease,"upload_requested",release=checked)
    _require_open(day, clock())
    try:
        video_id = upload_private(checked, media, meta)
    except Exception:
        raise PublicationHold("Private upload uncertain; reconcile without reupload") from None
    if not _id(video_id, _VIDEO):
        raise PublicationHold("No exact upload receipt; reconcile without reupload")
    checkpoint(lease,"private_id",release=checked,video_id=video_id)
    return _arm_known_private(checked,video_id,lease,clock=clock,checkpoint=checkpoint,
                              read=read,schedule_private=schedule_private)


def resume_known_private(day, lease, *, clock, load_pending, checkpoint, read, schedule_private):
    """One bounded continuation on a known receipt, not an uncertain intent.

    load_pending must read latest durable row, matching lease and phase
    private_id only, and confirm approval validity and no conflicting writer.
    The injected checkpoint must compare-and-set that exact revision and
    reject replay after schedule_requested. This path has no upload capability.
    """
    expected = _require_open(day, clock())
    if not isinstance(lease,str) or not lease:
        raise PublicationHold("Missing exact pending receipt")
    row = load_pending(lease)
    if not isinstance(row,dict) or row.get("phase") != "private_id" or row.get("lease") != lease:
        raise PublicationHold("Pending receipt changed or already has unresolved write intent")
    checked,vid = row.get("release"),row.get("video_id")
    if (not isinstance(checked,CheckedRelease) or checked.day != day
        or checked.channel_id != CHANNEL_ID or checked.publish_at != expected
        or not checked.first_review_id or not checked.final_review_id
        or not all(_id(x,_SHA) for x in (checked.recipe_sha256,checked.media_sha256,checked.metadata_sha256))
        or not _id(vid,_VIDEO)):
        raise PublicationHold("Private receipt does not match this KST release")
    return _arm_known_private(checked,vid,lease,clock=clock,checkpoint=checkpoint,
                              read=read,schedule_private=schedule_private)


def _arm_known_private(checked,video_id,lease,*,clock,checkpoint,read,schedule_private):
    day = checked.day
    _require_open(day, clock())
    if _verify(read,video_id,checked,publish_at=None) != "succeeded":
        return {"status":"private_processing_pending","date":day,"scheduled":False,
                "video_id":video_id,"reservation":lease}
    _require_open(day, clock())
    checkpoint(lease,"schedule_requested",release=checked,video_id=video_id)
    _require_open(day, clock())
    try:
        schedule_private(checked,video_id)
    except Exception:
        raise PublicationHold("Schedule update uncertain; reconcile exact private ID without retry") from None
    state = _verify(read,video_id,checked,publish_at=checked.publish_at)
    if state != "succeeded":
        raise PublicationHold("Scheduled asset processing changed; reconcile, do not retry")
    checkpoint(lease,"schedule_verified",release=checked,video_id=video_id)
    return {"status":"schedule_verified","date":day,"scheduled":True,
            "video_id":video_id,"reservation":lease,"publish_at":checked.publish_at}
