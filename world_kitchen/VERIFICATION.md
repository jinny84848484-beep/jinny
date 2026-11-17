# Implementation verification — 2026-11-16

- Initial baseline: `20571a993732fadea036da14e4b435aeb510224e`; integrated chief-of-staff guard from `d975ca8d8c6223f1dd32383f033e2d4ff780c121`.
- New cooking tests: 30 passed (including watt/time-pair and truthful first-frame ingredient-count guards).
- Repository offline suite: 478 passed, zero failures/skips (20.543 s), including existing 9 shared-guard tests.
- `compileall` and `git diff --check`: passed.
- Synthetic 12-second 4-card smoke render: completed with real ffmpeg; full decode passed; English SRT and result manifest generated. Synthetic colors/tone were used, not a newly generated/published recipe.
- First frame manually inspected for small wordmark, safe text bounds, full-image containment. Actual cooking episode media and new ElevenLabs provider calls were not used by this test.
- Empty queue CLI: `no_ready_episode`, uploaded=false.
- No production generation, upload, caption posting, or YouTube schedule performed by this implementation task.
- Initial renderer-stage limits were subsequently addressed by the end-to-end adapter extension below. Live credentials/scopes, reviewed brand asset delivery, human listening and actual production execution remain unverified.

## End-to-end adapter extension
- Implemented real HTTP adapters for bounded OpenAI research/image/review/transcription, ElevenLabs aligned synthesis, private YouTube upload, English captions, future scheduling and readback.
- 18 additional orchestration/adapter safety tests pass with mocked providers. Full isolated offline suite: 500 tests passed, zero failures/skips (23.770 s).
- `python -m world_kitchen.automatic` with default disabled flag: exits without provider calls.
- Local `--preflight` stopped before outbound requests: OPENAI_API_KEY, ELEVENLABS_API_KEY, KITCHEN_VOICE_ID absent. This does not establish whether GitHub Secrets exist.
- GitHub connector excludes secrets/variables management endpoints, so only workflow references are verified, not configured secret names/scopes.
- No live paid model, image, TTS, transcription, YouTube upload or scheduling call was made by this task.
- First render-path commit 2e61997d1db392db0602d9c825107affeea4956b passed remote `Shorts offline guards` run 43820380638. Extension CI must be checked separately.
- Review requested for source binding, gram-unit extension to shared guard, CAS before outbound submissions, English caption failure private hold, authentication scope and actual-model compatibility.

Independent code review fixes: watt/time pairs are matched together; exact caption receipt must be serving before publishAt is set; trusted BGM/rights/attribution configured; ASR and review reasons preserved; GitHub runtime restricted to main. Regression tests added.

Independent reviewer reread confirmed both blockers resolved and 45 focused tests passed. Upload/caption IDs are also fsync-saved in local receipt artifacts before a later journal save, for interrupted-CAS reconciliation.

Latest user correction: future first frames require truthful 재료 딱 N개! based on required ingredients; optional additions are excluded explicitly. Missing/false counts reject. 48 focused tests pass. Existing V8 video was not changed by code task.

## Concurrent retirement integration and review branch
- Integrated main through 429095ec34d3c2719afe8277e15be8b0844350ff without restoring retired corgi files. Removed all cooking runtime imports of retired modules; fixed-host transport is now world_kitchen/google_transport.py.
- During partial deletion, full suite exposed two missing-module import errors in remaining retired-channel tests. Upstream subsequently removed those tests; post-integration results below supersede that transient run.
- Cooking-focused suite: 50 passed including independent transport host/redirect tests.
- Extension is draft PR #1, NOT merged to main. Production flags and credentials unchanged; no live generation or posting.

Final integrated offline suite: 446 passed, zero failures/skips (19.362 s). Earlier 500-test count included retired-channel tests removed upstream; this is not a loss of cooking coverage. Initial PR CI on db817c2 failed during concurrent retirement; rerun required on integrated head.

## Claude review follow-up (2026-11-16)
Reproduced and corrected two blockers: research flags are cleared to false, structural validation is explicitly separate, and approval is assigned only after independent recipe/rights review with explicit true values, reasons and observed recipe/music-rights URLs. Final review requires explicit true no_unapproved_brands/no_unapproved_logos/no_faces fields and reasons; absent/false/string fields block upload. Approved channel wordmark is the sole logo exception. Repeated dishes now stop before paid independent review. Workflow default permissions are read-only; contents:write is job-scoped because the CAS journal must persist submission status before every outbound side effect. No authority/credential expansion or live calls.

Follow-up verification: 451 offline tests passed (20.267s), 55 cooking-focused tests passed. compileall and diff check passed. No live generation/upload.

## URL provenance follow-up
Earlier a8fd89a matched URLs to search results/citations, not necessarily opened pages. Cooking review now accepts only HTTPS URLs in completed web_search_call actions of type open_page, requires ALL recipe sources and music-rights URL to be opened and cited, and persists opened URLs with an explicit scope disclaimer. Search-only/citation-only/in-progress evidence rejects. This proves a provider page-open event, not complete content retrieval, truth, legal licensing, human reading, or rights ownership. Redirect/canonical URL mismatch fails closed. Reference: https://developers.openai.com/api/docs/guides/tools-web-search . No paid/live provider verification performed.
Local configuration existence (values not read/output): OpenAI/ElevenLabs/voice/OAuth and logo/music-path/rights/credit variables all absent. GitHub connector excludes Secrets API, so remote secret names/existence and scope remain unknown. Local V8 package assets exist but are not CI runner deployment evidence. Auth code checks returned youtube.force-ssl and exact channel; voice check matches configured voice ID plus Anna Kim name prefix, not a guarantee of voice quality or licensing. Brand/BGM legal provenance and remote deployment remain release blockers.

Verification: 453 offline tests passed (18.707s), 57 cooking-focused tests passed; compileall/diff check passed.

## 2026-11-17 live recovery follow-up
- Anna Kim existing voice ID actually produced the new caprese narration once within existing included credits. No extra subscription was purchased. Direct listening remains unperformed; technical audio and raw alignment checked.
- Preflight now resolves the configured voice via read-only GET /v1/voices/{voice_id}, avoiding false failure when a shared voice is absent from the account list. Approved ID and full Anna Kim display name are pinned by equality after independent review (prefix-only names and other well-formed IDs reject); no voice import or fallback.
- Recorded manually published 11/16 UQNDeho8LxI and 11/17 MlM-NJYHBkE in the dedicated cooking journal. These entries represent Studio-confirmed public videos, not automatic pipeline success.
- Full offline suite after these changes: 459 tests passed, zero failures/skips (21.567 s). Six regressions cover direct lookup, wrong identity, malformed ID, manual-post no-provider repeat, and both shipped manual receipts. git diff --check passed.
- No credentials generated, scopes expanded, gates enabled, merge performed, paid test repeated, or video uploaded by this change. OAuth, runner media deployment and OpenAI429 remain separate blockers documented in OPERATIONS-20261117.md.
