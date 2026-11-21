"""Read only the public repository's queue, with opaque status output.

The scheduled public Actions job does not render, cache, or export any
episode. Actual reviewed input stays in the approved private workspace.
"""
import json
from pathlib import Path

MAX_QUEUE_BYTES = 16 * 1024


def check_public_queue(path):
    """Return a deliberately opaque status; never follow manifest references."""
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_QUEUE_BYTES + 1)
        if len(raw) > MAX_QUEUE_BYTES:
            raise ValueError
        queue = json.loads(raw)
    except (ValueError, OSError, RecursionError):
        return 2, "public_queue_invalid"
    if not isinstance(queue, list):
        return 2, "public_queue_invalid"
    if queue:
        return 2, "public_queue_must_be_empty"
    return 0, "no_ready_episode"


def main():
    code, status = check_public_queue(Path(__file__).with_name("queue.json"))
    print(status)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
