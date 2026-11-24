"""Reviewed render handoff, dry-run by default. No network/credentials in CLI."""
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import subprocess
from typing import Protocol

from . import pipeline
from .publication import (
    CHANNEL_ID, KST, FinalReview, FirstReview, PublicationHold,
    metadata_digest, preflight, prepare_day,
)

_MANIFEST_CAP = 256 * 1024
_SRT_CAP = 128 * 1024
_MP4_CAP = 100 * 1024 * 1024
_CLOCK = lambda: datetime.now(KST)


@dataclass(frozen=True)
class RenderBinding:
    """Protected producer attestation, not scraped from local renderer JSON."""
    day: str
    channel_id: str
    recipe_sha256: str
    media_sha256: str
    english_srt_sha256: str
    rendered_at: datetime


@dataclass(frozen=True)
class TrustedReviewBundle:
    """Trusted loader verifies issuer/revocation/exact binding, never queue flags."""
    render: RenderBinding
    first: FirstReview
    final: FinalReview
    final_caption_sha256: str
    metadata: dict


@dataclass(frozen=True)
class LocalCandidate:
    day: str
    channel_id: str
    recipe_sha256: str
    media_sha256: str
    english_srt_sha256: str
    title: str
    contains_synthetic_media: bool
    description: str
    media: bytes = field(repr=False)


class ReleaseAdapter(Protocol):
    """Live transport, explicit injection only; journal and charge policy external."""
    def check_clear(self, release): ...
    def reserve(self, release): ...
    def checkpoint(self, lease, event, **state): ...
    def upload_private(self, release, media, metadata): ...
    def read(self, video_id): ...
    def schedule_private(self, release, video_id): ...


def _read_file(root, relative, cap):
    """Read fixed local regular files; no arbitrary filesystem/symlink access."""
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise PublicationHold("Unsafe local asset path")
    try:
        parents = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in relative.parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parents)
                os.close(parents)
                parents = child
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parents)
        finally:
            os.close(parents)
        with os.fdopen(fd, "rb") as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > cap:
                raise PublicationHold("Invalid regular local asset or size")
            raw = file.read(cap + 1)
            after = os.fstat(file.fileno())
    except OSError:
        raise PublicationHold("Local asset missing, unsafe or unreadable") from None
    if (len(raw) != before.st_size or after.st_mtime_ns != before.st_mtime_ns
        or after.st_size != before.st_size):
        raise PublicationHold("Local asset changed while reading")
    return raw


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError):
        raise PublicationHold("Invalid or ambiguous UTF-8 JSON") from None


def _queue_entry(queue, day):
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except (ValueError, TypeError):
        raise PublicationHold("Need canonical KST date") from None
    if not isinstance(queue, list) or any(not isinstance(e, dict) for e in queue):
        raise PublicationHold("Malformed cooking production queue")
    rows = [x for x in queue if x.get("date") == day]
    if len(rows) > 1:
        raise PublicationHold("Duplicate KST-day cooking queue entries")
    return rows[0] if rows else None


def _verify_mp4(video, expected_seconds):
    if len(video) < 16 or video[4:8] != b"ftyp":
        raise PublicationHold("Expected MP4 render, not a renamed placeholder")
    try:
        probe = subprocess.run(
            ["ffprobe","-v","error","-protocol_whitelist","pipe","-f","mov",
             "-show_entries","format=duration:stream=codec_type,codec_name,width,height",
             "-of","json","-i","pipe:0"],
            input=video, capture_output=True, timeout=12, check=True)
        details = _json(probe.stdout)
        streams = details["streams"]
        duration = float(details["format"]["duration"])
        hints = {s["codec_type"]:s for s in streams}
        picture, sound = hints["video"],hints["audio"]
        if (len(streams) != 2 or picture["codec_name"] != "h264" or sound["codec_name"] != "aac"
            or picture["width"] != pipeline.TEMPLATE["width"]
            or picture["height"] != pipeline.TEMPLATE["height"]
            or not math.isfinite(duration) or abs(duration - expected_seconds) > 0.35):
            raise ValueError
    except (KeyError,TypeError,ValueError,OSError,subprocess.SubprocessError,PublicationHold):
        raise PublicationHold("Local video format, duration or stream structure is not the expected render") from None


def _candidate(entry, day, repo_root, output_root):
    path = entry.get("manifest")
    if not isinstance(path, str):
        raise PublicationHold("Missing repo-relative recipe manifest")
    recipe_bytes = _read_file(repo_root, path, _MANIFEST_CAP)
    recipe = _json(recipe_bytes)
    if not isinstance(recipe, dict) or recipe.get("channel_id") != CHANNEL_ID:
        raise PublicationHold("Recipe manifest is not for the exact World Kitchen channel")
    try:
        duration = pipeline.validate(recipe)
    except (TypeError, KeyError, ValueError):
        raise PublicationHold("Recipe manifest fails local safety or template validation") from None

    # Same fixed day and filenames produced by runner.py/pipeline.py; never use
    # a path from result.json or let a queue row choose an unrelated video.
    video = _read_file(output_root, Path(day) / "video.mp4", _MP4_CAP)
    _verify_mp4(video,duration)
    video_hash = sha256(video).hexdigest()
    captions = _read_file(output_root, Path(day) / "english.srt", _SRT_CAP)
    if captions != pipeline.english_srt(recipe).encode("utf-8"):
        raise PublicationHold("Rendered English SRT differs from exact recipe")
    description = _read_file(output_root, Path(day) / "description.txt", _MANIFEST_CAP)
    if description != recipe["description"].encode("utf-8"):
        raise PublicationHold("Rendered description differs from exact recipe")
    result = _json(_read_file(output_root, Path(day) / "result.json", _MANIFEST_CAP))
    if (not isinstance(result, dict) or result.get("rendered") is not True
        or result.get("template_version") != pipeline.TEMPLATE["version"]
        or result.get("video_sha256") != video_hash
        or result.get("english_track_created") is not True
        or any(result.get(k) is not False for k in ("english_track_uploaded","uploaded","scheduled"))):
        raise PublicationHold("Local renderer receipt is wrong, stale, or already marked published")
    return LocalCandidate(day, CHANNEL_ID, sha256(recipe_bytes).hexdigest(), video_hash,
                          sha256(captions).hexdigest(), recipe["title"],
                          recipe["synthetic_disclosure"], recipe["description"], video)


def _trusted_packet(candidate, reviews):
    if not isinstance(reviews, TrustedReviewBundle):
        raise PublicationHold("No reviewed provenance from the trusted approval store")
    render = reviews.render
    if (not isinstance(render, RenderBinding)
        or (render.day,render.channel_id,render.recipe_sha256,render.media_sha256,render.english_srt_sha256)
        != (candidate.day,candidate.channel_id,candidate.recipe_sha256,
            candidate.media_sha256,candidate.english_srt_sha256)
        or reviews.final_caption_sha256 != candidate.english_srt_sha256):
        raise PublicationHold("Protected render or caption approval does not bind these exact files")
    metadata_digest(reviews.metadata)
    metadata = dict(reviews.metadata)
    if (metadata["title"] != candidate.title or metadata["description"] != candidate.description
        or metadata["contains_synthetic_media"] is not candidate.contains_synthetic_media):
        raise PublicationHold("Trusted metadata conflicts with rendered episode")
    return dict(recipe_sha256=candidate.recipe_sha256,rendered_at=render.rendered_at,
                media=candidate.media,metadata=metadata,first=reviews.first,final=reviews.final)


def run_release(queue, day, *, repo_root, output_root, clock=_CLOCK, load_trusted=None,
                live=False, adapter: ReleaseAdapter | None=None):
    """No upload/schedule/reservation at default. Production wiring is opt-in.

    load_trusted(candidate) must verify a trusted issuer and revoked/expired
    approvals and return immutable, exact-hash attestation from a privileged
    store. Raw GitHub artifacts, filesystem booleans, and this CLI cannot
    provide trusted approval. The real adapter enforces load/clock deadlines,
    current approvals at each write, CAS, and protected provenance.
    """
    entry = _queue_entry(queue,day)
    if entry is None:
        return {"status":"no_ready_episode","date":day,"scheduled":False}
    if entry.get("reviewed") is not True:
        return {"status":"awaiting_recipe_and_rights_review","date":day,"scheduled":False}
    if live is True and (adapter is None or not callable(load_trusted)):
        raise PublicationHold("No live transport or trusted approvals configured")
    candidate = _candidate(entry,day,repo_root,output_root)
    if load_trusted is None:
        return {"status":"dry_run_local_checks_only_approval_required","date":day,
                "channel_id":candidate.channel_id,"manifest_sha256":candidate.recipe_sha256,
                "video_sha256":candidate.media_sha256,"english_srt_sha256":candidate.english_srt_sha256,
                "scheduled":False}
    packet = _trusted_packet(candidate,load_trusted(candidate))
    if live is not True:
        checked = preflight(day,now=clock(),**packet)
        return {"status":"dry_run_reviewed_inputs_verified_not_scheduled","date":day,
                "channel_id":checked.channel_id,"publish_at":checked.publish_at,
                "video_sha256":checked.media_sha256,"scheduled":False}
    return prepare_day(queue,day,clock=clock,load_reviewed=lambda _:packet,
                       reserve=adapter.reserve,checkpoint=adapter.checkpoint,
                       check_clear=adapter.check_clear,upload_private=adapter.upload_private,
                       read=adapter.read,schedule_private=adapter.schedule_private)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root",default=".",type=Path)
    parser.add_argument("--queue",default="world_kitchen/queue.json")
    parser.add_argument("--output-root",default="runs/world_kitchen",type=Path)
    parser.add_argument("--date",default=None)
    args=parser.parse_args(argv)
    try:
        queue=_json(_read_file(args.repo_root,args.queue,_MANIFEST_CAP))
        print(json.dumps(run_release(queue,args.date or _CLOCK().date().isoformat(),
                                     repo_root=args.repo_root,output_root=args.output_root),ensure_ascii=False))
    except PublicationHold as exc:
        print(json.dumps({"status":"held","scheduled":False,"reason":str(exc)},ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
