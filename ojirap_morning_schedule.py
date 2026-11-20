"""Fail-closed scheduling core for Ojirap, with separately supplied owner adapters.

This module has no network calls or credentials. Running it requires wiring a
single CAS journal shared by the manual and automatic paths, plus an owner
adapter for YouTube private uploads, videos.update and videos.list.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
import re
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CHANNEL_ID = "UCZj1FlWDcoiEQNLfpwc5qtw"
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")

class ScheduleHold(RuntimeError):
    """An external result may need reconciliation; never retry it blindly."""

@dataclass(frozen=True)
class ReleaseWindow:
    day: date

    @property
    def target(self):
        return datetime.combine(self.day, time(7), KST)

    @property
    def cutoff(self):
        return self.target - timedelta(minutes=20)

    @property
    def start(self):
        return self.target - timedelta(hours=2, minutes=15)

    @property
    def publish_at(self):
        return self.target.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def require_open(self, now):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ScheduleHold("An aware clock is required")
        if not self.start <= now < self.cutoff:
            raise ScheduleHold("No longer in the preparation window; never publish immediately")

@dataclass(frozen=True)
class Job:
    job_id: str
    topic_key: str
    file_sha256: str
    channel_id: str
    editorial_passed: bool
    rights_passed: bool


def stage_and_schedule(window, jobs, *, clock, reserve, checkpoint, upload_private,
                       read_youtube, schedule_existing):
    """Reserve BOTH slots, stage BOTH files privately, then verify each reservation.

    reserve(window,jobs): one atomic insert or conflict across all managed paths.
    checkpoint(event, **state): durable compare-and-set; raise on any conflict.
    upload_private(job): creates a never-public, unscheduled video; returns its ID.
    read_youtube(id): verify owner/channel/private/processing and schedule readback.
    schedule_existing(id, ISO): schedule the known private ID; never creates a video.

    The YouTube scheduling calls are not atomic. If the second arm fails, leave the
    first verified receipt in the journal and the unresolved intent locked. This
    function does not retry, hide, or silently mark a partial outcome 'complete'.
    """
    jobs = tuple(jobs)
    window.require_open(clock())
    if len(jobs) != 2 or len({j.job_id for j in jobs}) != 2 or len({j.topic_key for j in jobs}) != 2:
        raise ScheduleHold("Two distinct reviewed stories are required")
    if len({j.file_sha256 for j in jobs}) != 2 or any(
        not j.job_id or not j.topic_key or j.channel_id != CHANNEL_ID
        or not j.editorial_passed or not j.rights_passed
        or not SHA256.fullmatch(j.file_sha256) for j in jobs
    ):
        raise ScheduleHold("Wrong channel, unapproved material or invalid media hash")

    reservation = reserve(window, jobs)
    if not isinstance(reservation, str) or not reservation:
        raise ScheduleHold("Daily shared reservation unavailable or conflicting")
    ids = []
    for slot, job in enumerate(jobs, 1):
        window.require_open(clock())
        checkpoint("upload_requested", reservation=reservation, slot=slot, hash=job.file_sha256)
        window.require_open(clock())
        video_id = upload_private(job)
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in ids:
            raise ScheduleHold("Wrong or unknown private upload receipt")
        checkpoint("private_id", reservation=reservation, slot=slot, video_id=video_id)
        ids.append(video_id)

    def verify(video_id, when):
        found = read_youtube(video_id)
        if (found.get("id") != video_id or found.get("channel_id") != CHANNEL_ID
            or found.get("privacy") != "private" or found.get("processed") is not True
            or found.get("ever_public") is not False or found.get("publish_at") != when):
            raise ScheduleHold("YouTube state differs from the verified private release plan")

    # No schedule changes if either asset is absent, unprocessed or not private.
    for video_id in ids:
        verify(video_id, None)
    for slot, video_id in enumerate(ids, 1):
        window.require_open(clock())
        checkpoint("schedule_requested", reservation=reservation, slot=slot,
                   video_id=video_id, publish_at=window.publish_at)
        window.require_open(clock())
        schedule_existing(video_id, window.publish_at)
        verify(video_id, window.publish_at)
        checkpoint("schedule_verified", reservation=reservation, slot=slot, video_id=video_id)
    checkpoint("both_scheduled", reservation=reservation, video_ids=tuple(ids),
               publish_at=window.publish_at)
    return tuple(ids)
