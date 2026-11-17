# Two-preview recovery, 2026-11-17

Manual mode: `operator_preview_20261117`. Never invoke the old draft retry modes.

Pinned inputs: #42 submitted record, two Korean Ojirap stories, exact independently reviewed job/media package, ten actual PNGs, and existing voice/model config. Source fact and PNG checks are PASS_PRE_RENDER_ONLY; this is not final AV QA or publication clearance.

The pre-render review combines remote main ledger and coordination's direct YouTube Studio inventory snapshot (22 public Shorts, no private/scheduled upload, no 11/17 upload). Recheck current inventory before any later upload.

No OpenAI endpoint is called. ElevenLabs read-only voice/remaining-included-quota checks and exact YouTube channel verification remain required. Full TTS speech is 381 and 373 characters including delivery tags; config is hash-pinned, max 900 characters per item, at least 1800 included credits at preflight and current included balance checked immediately before each synthesis. No overage/top-up/subscription change is implemented. Provider dollar billing is not inferred from character counts.

Original failed checkpoints and a deep copy of the complete original record remain in the journal. CAS occurs before any paid synthesis; exact record hash forbids reentry. Each TTS has a new submitted marker; failures do not retry. Existing synthesis cache/usage lock are reused.

There is NO upload call in this mode. Both previews end at `operator_final_qa_pending`. Inspect actual video/audio, captions, framing, duration, metadata, and renewed duplicate evidence before designing/authorizing a later upload continuation. Do not set a generic reviewed status to bypass this hold.

Artifacts: `runs/2026-11-17/ojirap/<job-id>/`, review package/original record in `runs/2026-11-17/recovery/`. Failure after one synthesis can leave one completed preview; preserve it and reconcile, never rerun this mode blindly.

Reproduce offline guard tests: `python tests/run_offline.py` (network blocked).
