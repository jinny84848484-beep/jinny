# World Kitchen producer → private release handoff (design only)

Decision: prohibit plaintext production artifacts on the existing public repository before putting the first approved episode in the queue. Before this draft the public workflow uploaded the entire `runs/world_kitchen/` and state path as a seven-day artifact with `if: always()`, and caches the local render ledger. The queue is empty in the last confirmed main read. This is a configuration exposure path, not evidence any episode or credentials have leaked. This draft restricts the 17:00 public workflow to an empty-queue check, with no cache or artifact upload. Preserve public fixture-only tests; do not provision or activate production on this design alone.

Official references:
- Repository public label: https://github.com/jinny84848484-beep/jinny
- Current producer workflow: https://github.com/jinny84848484-beep/jinny/blob/main/.github/workflows/world-kitchen.yml
- Who can retrieve artifacts: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts
- Artifact metadata and ZIP access: https://docs.github.com/en/rest/actions/artifacts
- Run/attempt details: https://docs.github.com/en/rest/actions/workflow-runs
- Workflow-run trust separation and risks: https://docs.github.com/en/actions/reference/security/secure-use
- OIDC trust claims if using object storage: https://docs.github.com/en/actions/reference/security/oidc

## Preference: first approved episode, least machinery

1. Keep public GitHub limited to code, blank/example queue, and synthetic test results. Trusted isolated producer/reviewer with ephemeral scratch receives approved manifest and assets through a private owner-controlled transfer or existing local protected workspace. No external public commit for titles, queue, raw sources, captions, reviews, or pending episodes. Signed private receipt binds bytes to the particular render and its source revision. Start in a non-posting dry run. Avoid new infrastructure where a known approved private workspace can meet the same requirements.
2. Run a managed producer's pinned code against immutable trusted manifest and input object versions. Capture exact repo ID, approved full commit SHA, allowlisted workflow or entrypoint/version, run ID and run attempt, date KST, channel ID, renderer version, source manifest digest, per-input digests/versions, output names and sizes, MP4/SRT/description/result digests, rendered UTC time and not-after. Bind that private receipt to a trusted producer identity/signature; uploader name or a hash embedded in its ZIP is not sufficient.
3. Transfer only a bounded, strictly named bundle: source manifest if needed by the current runner, `video.mp4`, `english.srt`, `description.txt`, `result.json`, and signed receipt. Keep reusable raw scene plates and source media out unless specifically authorized. Reference recipe/safety/rights and downstream approvals from the protected store; don't serialize private user approvals into the transport. Put-if-absent in a truly private store with narrow writer/reader permissions; no public URL, no dump of signed links/keys/content in logs, no GitHub cache of production data. A separate private GitHub repository may use official Actions artifacts if every repository-read identity is authorized for that material, including its logs; don't repurpose existing public artifact ZIPs.
4. Private receiving environment verifies exact bytes, per-file bounded sizes, no archives nested, no symlinks/duplicates/mismatched or escaping paths, exact source and rendered-day binding, trusted producer signature, expiry and pinned revision. Unpack only data into new isolated directory. Do not execute downloaded code, refetch source from mutable main, or reuse a stale local path. Populate already defined `release_runner` layout from the verified manifest and exact files; transport receipt and verifier attach to the protected `RenderBinding`. Final approval happens *after* visual/listening/caption QA and separately checks the pinned MP4, metadata, final English SRT, date, channel, and schedule; a well-formed producer receipt alone is not a publication decision.
5. Record one accepted candidate per KST day/channel in the protected CAS journal before beginning writes; repeat receipt fetches only read the same ID/bits. Hold conflicts, duplicates, producer timeout, missing day, failed/old build, conflicting revisions, revocation, or cutoff. Verify exactly one owner channel and exact initial private ID; no API write from artifacts. Continue known processing only through the same durable receipt and before 18:40 KST; do not repeat uncertain upload/schedule. After cutoff keep private unless specially authorized reconciliation; do not roll date. Default manual dry run never reserves or writes remotely.

## If official GitHub cross-run artifact transport must be used

- Require same-repo protected/pinned producer workflow, exact trusted full `head_sha`, exact `workflow_id`, expected event (schedule or authorized manual), `head_repository_id == repository_id`, exact `run_id` and `run_attempt`, and successful conclusion from official Actions REST. Reject PR/fork/unexpected event/context; no lookup like latest, branch-current content, newest ZIP, fallback attempt, rerunner name, or mutable cache. Pin consumer code independently; keep publisher credentials only in its protected environment. No workflow_run checkout at an untrusted producer ref. If workflow_run is later chosen, it is a notification only; all gates above must pass before reads and writables.
- Use one artifact name tied to KST date, exact run ID/attempt or an opaque token mapped in the protected journal. Use distinct attempt and no overwrite. Verify one exact assigned artifact ID post-run; REST metadata must agree on repository/run/head SHA, size limit, creation timestamp, expiry and digest. Because artifact metadata may not separately provide attempt, verify the exact attempt endpoint and also bind attempt to the signed bundle and registered artifact reference, not name alone. Compare downloaded ZIP/archive bytes to the GitHub SHA-256 where semantics match the pinned artifact version, plus mandatory independently signed inner hashes. Ordinary download-action warning alone is not a fail-closed verification.
- Public GitHub: **ciphertext only** if an owner-approved encrypted transport is necessary. Encrypt before passing bytes to Actions (existing staged_transfer helper is an example of bounded signed X25519/AES-GCM envelope handling, but legacy news context/keys must not be reused or treated as already approved). Recipient keys verified independently, recipient private key never in producer/public repo, context authenticated includes repository/run/attempt/date/channel/key IDs. Keep titles, dates if private, filenames, content, approvals and secret-bearing state inside ciphertext or private store; translate only harmless opaque ID to public metadata. No plaintext outputs/errors or restore from public cache. Encryption does not make an untrusted public producer safe, nor replace repo authorization. A new channel-scoped crypto integration gets separate review; use a private runner for raw material when public-runner access/log exposure is not authorized. If public metadata/timing alone is confidential, skip public GitHub artifact entirely.
- Privately stage and process only verified data; the receiver consumes by exact ID once, has bounded parsing/CRC and archive size checks, obtains fresh owner approval before writes, and rejects concurrent alternate candidates by CAS. Protected owner approval stays independently revocable.

Pseudocode:
```
expected = protected_case(day_kst, WORLD_KITCHEN_CHANNEL)
if not expected.approved_recipe: return NO_READY_EPISODE
run = get_workflow_run_attempt(expected.repository_id, expected.run_id, expected.attempt)
require(approved_workflow_context(run, expected) and run.success and now < cutoff)
meta = get_exact_artifact(expected.artifact_id)
require(exact_meta_binding(meta, run, expected) and meta.unexpired)
zip_bytes = bounded_official_download(meta)
require(artifact_digest_matches(meta, zip_bytes))
bundle = safe_unpack_or_decrypt_then_unpack(zip_bytes)
binding = verify_trusted_producer_receipt(bundle, expected)
require(exact_member_hashes(bundle, binding) and binding.fresh_for(day_kst))
stage_verified_data_only(bundle)
review = trusted_approval_store.read_exact(binding)
return release_runner.dry_run(binding, review)  # no authorization promotion
# Separately authorized production entrypoint: exact same binding/approval + protected CAS.
```

Current blockers: no approved episode, no configured protected handoff store/receiver, no production owner transport and operational cutover; proposed changes need offline test fixture and private read-only end-to-end rehearsal before actual activation. No current repo/settings/production changes.
