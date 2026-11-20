# Ledger adapter and processing poll hook (unwired, offline-tested)

- Instantiate GitHubLedgerCAS with the existing cloud_runner.journal_get and
  journal_put callbacks, only for the reviewed repository/default branch
  and state/ledger.json. Pass its read/write methods to the shared slot
  module. This reuses the blob SHA; no credential is loaded by this module.
- A pre-PUT GET SHA mismatch is the only KnownConflict. A racing PUT still
  includes the expected SHA. GitHub documents 409 as Conflict, and 422 as
  validation/spam. Neither is a safe stale-SHA auto-retry signal through
  this transport. Any error after PUT attempted means no reissue; inspect
  the durable journal to determine what happened. The blob identity is not
  a monotonically increasing commit counter; do not roll back journal bytes.
- Once both exact IDs and the awaiting_processing manifest are durable,
  call poll_resume (also usable following a job restart). The default
  15/30/60s intervals allow at most four checks; hard wall-cutoff and
  150-second monotonic budget apply to the guard, including send admission.
  Network adapters must set I/O timeouts; this does not interrupt an active
  network request. Poll only ProcessingPending; terminal and divergent
  state or attempted schedule timeouts hard-stop. Resume has no upload
  capability. Receipts and intent CAS must use the current durable revision,
  and stale/unknown intents must not be reissued.
- This does **not** wire the PR's manifest callbacks and per-slot transition
  logic into the production runner, authorize OAuth scopes, alter cron,
  or protect legacy publish paths. Wire the manifest phase, slot reservation,
  and day/completion projection through a reviewed shared state adapter
  before enabling; direct Studio writes remain outside enforcement.

GitHub contract: https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#create-or-update-file-contents
