# VidoGrid preparation scaffold: OFFLINE / NOT ACTIVE

Run `python corgi_preparation.py` to print a draft. No generation workflow or schedule is added. No API key is read from the environment, no account request is sent, no paid request is made, and approved publishing queue is never edited.

Pipeline boundary: draft → official request schema validation → disabled live adapter → technical probe report → human content/rights review → separately approved publishing queue. Provider success is not QA or publication approval. Current character references, completed original assets, quality review and rights confirmation remain missing. Text-only Veo draft is not character-consistent finished production; image references and editing are not implemented here.

Official sources consulted 2026-11-13:
- https://docs.vidogrid.ai/resources/video-generation
- https://docs.vidogrid.ai/getting-started/billing-credits
- https://docs.vidogrid.ai/resources/history-apis

Veo POST uses multipart `prompt`, `model`, `resolution`, `aspect_ratio`, `duration`; header is `x-api-key`. Accepted response contains `uuid`, `estimated_credit`. Account uses `plan_id`, `is_active`, `user_credit.available_credit`. History status 2 contains `generated_video`; status 3/-2 is not usable success. Only validated text-only Veo options are modeled. No response examples or personal account data are saved.

COST WARNING: there is no verified fixed 4-credit cap for these options. Official docs say credits are reserved on acceptance; example 20 is illustrative. A current exact price/charge upper bound and explicit per-request owner approval are needed BEFORE a real request, not after `estimated_credit` returns. Live submission is therefore hard-disabled even with a key and budget. Default budget zero blocks before any transport. Current account reported upstream as Free Trial/zero credits; this scaffold has not queried it.

Mock harness only: `mock_submission` accepts an injected fake transport plus synthetic account. It requires exact-request-hash cost approval and adequate Premium credits. A local O_EXCL receipt is fsynced BEFORE the simulated POST, so an ambiguous response or existing receipt cannot auto-retry. No provider idempotency header is documented. Receipts are private local artifacts, not public repository data. Before live/CI implementation, use remote durable compare-and-swap journal/locks surviving runner destruction and account-wide spend reconciliation. Never replace this missing persistence with a post-upload commit. No automatic retry or paid regeneration.

Technical probe reporting covers only supplied ffprobe metadata. It does not inspect pixels/audio or certify rights. Human review and owner public approval always remain pending. Approved handoff requires an actual reviewed MP4, SHA256, sanitized rights/QA evidence, final metadata and exact owner public approval via the separate corgi queue. No automatic approvals are fabricated.
