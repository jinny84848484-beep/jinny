# Corgi daily approved-artifact publisher

Schedule: every day 19:00 KST (10:00 UTC), sharing `shorts-production` concurrency.
GitHub cron can be delayed. The publisher rejects runs outside 19:00–19:59 KST rather than posting late. Manual dispatch defaults to validation only.

Current state: queue EMPTY; no approved finished video exists here. Schedule wiring is provided, but actual publication is BLOCKED. OAuth availability and channel ownership are runtime-only checks, not verified by offline CI. This does not generate videos, consume paid generation credits, or revive English news. Ojirap's existing schedule is unchanged.

## Adding an explicitly approved artifact

Store an MP4 (at most 100 MiB) and JSON evidence files under `corgi/` in the repository. No external download links or credentials. The repository is public: only store artifacts and evidence already approved for public sharing. Preserve original private approval evidence elsewhere and use a non-sensitive reference here.

Add one entry per KST date to `queue.json` (`schema_version: 1`, `items: [...]`):
- `id`: unique alphanumeric/underscore/hyphen ID
- `publish_date_kst`: YYYY-MM-DD
- `channel_id`: UCWFQVq3zxSnMi1sa0dUTVMQ
- `asset`: repository relative `corgi/...mp4`
- `sha256`: exact MP4 SHA256
- `metadata`: title, description, boolean `made_for_kids`, boolean `contains_synthetic_media`
- `qa` and `rights`: each `{ "path": "corgi/...json", "sha256": "..." }`
- each evidence JSON: `approved: true`, exact `asset_sha256`, nonempty `reviewer`, nonempty `evidence`. QA must document actual video/audio review; rights must document original ownership or permission for every third-party component. Do not invent approval.
- `approval`: `decision: "public"`, `owner_approval_reference`, `manifest_sha256`. The last is SHA256 of canonical JSON of the entire entry excluding `approval` (Python `json.dumps(...,sort_keys=True,ensure_ascii=False,separators=(',',':'))`). Approval must be from the owner for that exact artifact and final metadata. Altering any part invalidates it.

Reuse the existing `YOUTUBE_KOREAN_OAUTH` secret ONLY for this corgi workflow. It must contain the matching channel ID, client ID, client secret and refresh token. Runtime checks `channels?mine=true` before submission. No secret value is stored in the queue/logs.

## Durable journal and uncertain results

The existing remote `state/ledger.json` is updated with a separate `corgi_publications` map. Existing records remain untouched. A GitHub SHA-checked write of `upload_submitted` MUST succeed before upload starts. No upload happens if that write fails. After upload, preserve the video ID in `verification_pending`, then GET the video's actual channel/public status before `complete`.

Any incomplete journal entry blocks further corgi uploads. A timeout can mean the video exists: do not clear the entry or rerun blindly. An operator must inspect YouTube Studio, reconcile actual video ID/visibility, and record the resolution. Duplicate date, asset hash and item ID are blocked. HTTP errors never print OAuth/session tokens. CI only performs mocked offline validation and does not prove actual publishing.

Daily scope is one approved video per date; any separate request for two consecutive uploads is not automatically covered by this queue. Evidence should be a sanitized reference plus review conclusion, never private drafts, user chats, signed URLs, credentials, or personal-data-containing license documents.

## Read-only authentication check

`corgi-auth-check.yml` runs manually or when its own checker/workflow changes on main. Its standalone standard-library checker only refreshes the existing OAuth token and reads `channels?mine=true`. It rejects redirects and never uploads, alters videos, writes the ledger, or invokes paid generation. Success proves current authentication and target channel identity only; upload permissions, quota, processing and public visibility are not proven. Errors expose fixed local codes only, not credentials or provider payloads. No scheduled authentication polling is added.
