# 07:00 shared CAS state bridge (offline tested, unwired)

Construct MorningState(GitHubLedgerCAS(journal_get, journal_put), window,
reservation_owner_id, clock=aware_clock) once per worker; the opaque owner
reference must stay identical across restarts for the specific managed run.
Initial core calls use instance.reserve and instance.checkpoint.
Polling on restart uses a new instance with the same owner/day and its load
and checkpoint methods. Recommended poll_resume remains read-only until
the exact two video IDs have passed all readiness checks.

The same ledger contains per-slot phases under ojirap_base_slots_v1 and a
day-level exact private manifest under ojirap_morning_manifests_v1. The latter
is written by CAS only after both bound slots hold distinct private IDs.
A restarted load verifies both slot phases and all manifest data at the same
revision. A slot intent CAS serializes workers; if a peer already wrote, no
provider call is authorized. Partial or unknown intent requires separate
reconciliation. No recovery re-upload or new pair claim for the same day.

With two verified schedule receipts, update the manifest to both_scheduled.
It is not days[day].complete and not proof of public visibility. If a worker
crashes between the last receipt and the final manifest write, verify those
existing IDs and reconcile; no new booking is performed automatically.
If it crashes before the private manifest commit, fail closed rather than
guessing what happened. Never roll back raw ledger data to restart a run.

This bridge owns no provider callbacks or credentials. Existing live runner
and manual paths still require integration, tested live state readback,
rights and credential verification, and explicit deployment decisions.
