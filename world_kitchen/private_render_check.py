"""Offline render and local exact-media check; no publication path."""
import argparse
from copy import deepcopy
from datetime import datetime
from functools import partial
from hashlib import sha256
import json
import os
from pathlib import Path

from . import pipeline, runner
from .publication import KST, PublicationHold
from .release_runner import _json, _queue_entry, _read_file, run_release

_MANIFEST_LIMIT = 256 * 1024
_ASSET_LIMIT = 50 * 1024 * 1024
_PACKAGE_LIMIT = 160 * 1024 * 1024


def _private_dir(path):
    """Require a real caller-owned directory, not accessible to group/others."""
    try:
        requested = Path(path).expanduser().absolute()
        canonical = requested.resolve(strict=True)
        info = canonical.stat()
    except (OSError, RuntimeError):
        raise PublicationHold("Provide an existing owner-private input/output directory") from None
    if (requested != canonical or not canonical.is_dir() or info.st_uid != os.geteuid()
        or info.st_mode & 0o077):
        raise PublicationHold("Input/output directories must be owned by you, unlinked and mode 0700")
    return canonical


def _write_new(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def _media_format(raw, image):
    if image:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", ".png"
        if raw.startswith(b"\xff\xd8\xff"):
            return "jpeg", ".jpg"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "webp", ".webp"
        raise PublicationHold("Use PNG, JPEG or WebP for local still assets")
    if raw[:4] in (b"RIFF", b"RF64") and raw[8:12] == b"WAVE":
        return "wav", ".wav"
    if raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 255 and raw[1] & 0xe0 == 0xe0):
        return "mp3", ".mp3"
    if raw[4:8] == b"ftyp":
        return "mov", ".m4a"
    raise PublicationHold("Use local WAV, MP3 or M4A audio, not a playlist or URL")


def _local_render_manifest(recipe, in_root, attempt):
    staged = deepcopy(recipe)
    assets, audio_formats = {}, {}
    remaining = _PACKAGE_LIMIT

    def stage(path, image):
        nonlocal remaining
        if not isinstance(path,str) or not path or "://" in path:
            raise PublicationHold("Asset must be a relative local private input")
        key = (path,image)
        if key not in assets:
            raw = _read_file(in_root,path,min(_ASSET_LIMIT,remaining))
            fmt,extension = _media_format(raw,image)
            path_copy = attempt / f"asset-{len(assets):02d}{extension}"
            _write_new(path_copy,raw)
            assets[key] = (str(path_copy),fmt)
            remaining -= len(raw)
        return assets[key]

    staged["logo"],_ = stage(recipe.get("logo"),True)
    for scene in staged["scenes"]:
        scene["image"],_ = stage(scene["image"],True)
    staged["narration_audio"],audio_formats["narration_audio"] = stage(recipe.get("narration_audio"),False)
    if recipe.get("music"):
        staged["music"],audio_formats["music"] = stage(recipe["music"],False)
    return staged,audio_formats


def render_and_check(*, private_input, private_output, day, queue_name="queue.json"):
    """Only the existing local renderer and its default local-only checks."""
    in_root,out_root = _private_dir(private_input),_private_dir(private_output)
    checkout = Path(__file__).resolve().parents[1]
    if (in_root == out_root or in_root in out_root.parents or out_root in in_root.parents
        or any(root == checkout or checkout in root.parents for root in (in_root,out_root))):
        raise PublicationHold("Use separate private input and output roots outside the repository")
    queue = _json(_read_file(in_root,queue_name,_MANIFEST_LIMIT))
    entry = _queue_entry(queue,day)
    base = {"date":day,"uploaded":False,"scheduled":False}
    if entry is None:
        return {**base,"status":"no_ready_episode"}
    if entry.get("reviewed") is not True:
        return {**base,"status":"awaiting_recipe_and_rights_review"}
    manifest_name = entry.get("manifest")
    if not isinstance(manifest_name,str):
        raise PublicationHold("Missing private relative recipe manifest")
    recipe_bytes = _read_file(in_root,manifest_name,_MANIFEST_LIMIT)
    recipe = _json(recipe_bytes)
    try:
        pipeline.validate(recipe)
    except (AttributeError,TypeError,KeyError,ValueError):
        raise PublicationHold("Private recipe failed local validation") from None

    # Output date claim is atomic on the same local private filesystem. Do not
    # rely on the legacy runner's non-exclusive JSON alone for concurrent calls.
    locks = out_root / ".attempts"
    try:
        locks.mkdir(mode=0o700,exist_ok=True)
        if _private_dir(locks) != locks:
            raise PublicationHold("Invalid private attempt directory")
        attempt = locks / day
        if os.path.lexists(out_root/day):
            return {**base,"status":"existing_attempt_requires_reconciliation"}
        try:
            attempt.mkdir(mode=0o700)
        except FileExistsError:
            return {**base,"status":"existing_attempt_requires_reconciliation"}
    except OSError:
        raise PublicationHold("Cannot claim a private render attempt") from None

    try:
        # Retain exact original bytes for the check. The execution copy differs
        # only in file paths that now point at immutable per-attempt snapshots.
        _write_new(attempt/"source.json",recipe_bytes)
        staged,audio_formats = _local_render_manifest(recipe,in_root,attempt)
        execution = attempt/"execution.json"
        staged_bytes = json.dumps(staged,ensure_ascii=False).encode("utf-8")
        _write_new(execution,staged_bytes)
        production_queue = [{"date":day,"reviewed":True,"manifest":str(execution)}]
        result = runner.run(production_queue,attempt/"state.json",out_root,day,
                            renderer=partial(pipeline.render,local_audio_formats=audio_formats))
        if result.get("status") != "rendered_requires_visual_audio_and_caption_review":
            raise PublicationHold("Renderer did not complete the required private output")
        prepared = [{"date":day,"reviewed":True,"manifest":"execution.json"}]
        outcome = run_release(prepared,day,repo_root=attempt,output_root=out_root)
        staged_hash = sha256(staged_bytes).hexdigest()
        if (outcome.get("status") != "dry_run_local_checks_only_approval_required"
            or outcome.get("manifest_sha256") != staged_hash):
            raise PublicationHold("Private dry-run did not verify exact local output")
        return {**outcome,**base,"status":"rendered_and_locally_checked_review_required",
                "source_manifest_sha256":sha256(recipe_bytes).hexdigest(),
                "staged_manifest_sha256":staged_hash}
    except Exception:
        # Keep the exclusive marker even for partial outputs or uncertain runs;
        # disclose neither source path nor provider/renderer diagnostics here.
        raise PublicationHold("Private attempt stopped; retained for local reconciliation, not auto-retried") from None


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--private-input",required=True)
    p.add_argument("--private-output",required=True)
    p.add_argument("--date",default=datetime.now(KST).date().isoformat())
    p.add_argument("--queue",default="queue.json",help="Path relative to private input")
    args=p.parse_args(argv)
    try:
        result = render_and_check(private_input=args.private_input,private_output=args.private_output,
                                  day=args.date,queue_name=args.queue)
    except PublicationHold as exc:
        print(json.dumps({"status":"held","uploaded":False,"scheduled":False,"reason":str(exc)},ensure_ascii=False))
        return 2
    print(json.dumps(result,ensure_ascii=False))
    return 2 if result["status"]=="existing_attempt_requires_reconciliation" else 0


if __name__=="__main__":
    raise SystemExit(main())
