# 07:00 local readiness CLI (draft-only)

Run on Linux in the 04:45–06:40 KST preparation window:
```sh
python ojirap_morning_preflight.py /path/to/reviewed-pair --day 2026-11-21 \
  --approved-manifest-sha256 APPROVED_SHA_FROM_SEPARATE_REVIEW_CHANNEL \
  --local-journal /path/to/read-only-ledger-snapshot.json
```
Exit 0 means ready **for a dry run only**. No flag enables publication,
private staging, slot reservation, network, workflow dispatch or credentials.
Without the independent approval digest or a journal snapshot, the CLI can
check local inputs but exits 2 (not ready). The caller is responsible for
getting the digest via a trusted review/approval channel. Hashing an
untrusted manifest and passing that digest does not prove review. The tool
does not authenticate a signature or establish the identity of the reviewer.

The package directory contains manifest.json, slot-1.mp4 and slot-2.mp4.
Manifest exact keys: version (int 1), day (ISO date), channel_id (pinned
Ojirap owner channel), publish_at (07:00 KST canonical UTC from ReleaseWindow),
reviewed_at (aware timestamp, no more than 36 hours old), jobs (ordered
two entries). Each job has slot (int 1/2), job_id (day-prefixed),
topic_key, file (fixed basename for that slot), sha256, channel_id,
editorial_passed, rights_passed and audio_qa_passed (all literal true).
These flags only carry trusted review results; this tool verifies matching
bytes, not playable video, content rights or editorial facts independently.

A library caller can inject read_journal() yielding (snapshot, revision);
there is no write callback or network implementation here. This checks
existing morning state and in-memory slot policy for any same-day effects.
The CLI local snapshot digest is only for simulation, not a GitHub revision
and not necessarily fresh. Preflight produces no lease; later approved
execution still needs a fresh CAS and exact media hash recheck.
