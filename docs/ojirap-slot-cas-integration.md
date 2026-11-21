# Shared Ojirap base-slot CAS (offline; not wired)

Schema: state/ledger.json's ojirap_base_slots_v1 maps ISO Asia/Seoul
day to slot keys "1", "2". Each slot is scoped to the exact Ojirap channel
ID and stores managed kind, opaque owner reference, job, canonical topic,
approved local media SHA-256, phase and optional verified YouTube ID.
Do not include credentials, resume URLs, descriptions or private payloads.
This is distinct from additional_batches and legacy publications.

Use cloud_runner.journal_get / journal_put on the same existing file. Staff
must implement an adapter that maps only explicit, known no-write SHA
mismatches to KnownConflict. Do not reinterpret timeouts, generic 409/422,
network or transport errors as known conflicts. Treat missing SHA as hold,
not first-file creation. The only automatic CAS retry is bounded acquisition
before any provider calls; each retry reads the whole latest snapshot.

1. For integrated auto release, claim both slots in one CAS. Manual managed
   publisher claims one or two before sending; only an authorized extra path
   can use the extra ledger, with its existing gates. To gate paid research
   before file hashes exist, require an earlier day-level preflight check
   or introduce an explicit placeholder reservation contract; this module
   strictly claims final hash-bound jobs, not placeholders. Archive prior-day
   entries without altering live day leases or same-topic history.
2. Bind PR #2 reservation and checkpoint callbacks to this CAS revision.
   The morning guard retains both exact private IDs, release window,
   processing readback and future publishAt checks. Record each outgoing
   intent atomically and check the pinned date/window again immediately
   before the send. A successful fresh intent CAS grants only that call to
   the worker that wrote it; load() may inspect it but cannot grant replay.
   After receiving an ID/readback, write the receipt with the same lease
   revision. A known disjoint CAS after a provider call is a reconciliation
   event, never reason to resend. For resume, use readback for known IDs;
   do not create a replacement if a prior result is unknown. The caller
   must prove owner/channel, current processing status and final visibility.
3. A finished public receipt must be projected into publications only upon
   independent public verification. Preserve older audit data. New managed
   receipt rows should bind explicit KST day and slot. Do not mark a day
   complete if only one publication or any unresolved effect exists.
4. Live activation still blocked until shared adapter, manual path, schedule
   scope and all legacy production entry points are integrated or disabled.
   Old produce/publish_reviewed and direct Studio/vidIQ flows do not honor
   this module. Explicit writer/lease checks must be shared BEFORE enabling
   the new scheduled mode. Holding both exact files privately is not proof
   they were scheduled or published, and dual schedule updates are not atomic.

Testing is in-memory CAS only, no external writes. This file does not deploy,
alter permissions, change a workflow, schedule tasks or upload anything.
