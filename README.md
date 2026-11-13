## 현재 생산 범위 (2026-11-13 변경)

매일 08:00 KST 뉴스 생산은 **오늘의 오지랖 한국어 2편만** 대상으로 한다. 동방/`korean` 영어 뉴스 조사·대본 생성·보정·검수·음성·영상·업로드는 중단했다. 과거 4편 기록과 채널 설정은 역사 검증용으로 보존하지만, 영어 작업이 들어 있는 기존 복구 배치는 자동 재개하지 않고 차단한다. 새 일일 대본은 오지랖 2개 이슈만 허용한다. 영어 OAuth secret은 삭제하지 않았으나 일일 workflow에는 전달하지 않는다. 기존 원장 보류와 재시도 방지 규칙은 유지하며 운영 재실행은 이번 변경에 포함되지 않는다.

코기 19:00 자동 제작·게시 경로는 아직 미활성이다. 최종 영상, 권리·품질 검수, 업로드 어댑터와 중복 방지 검증이 완료되기 전 자동 게시 성공으로 표시하지 않는다. 아래 옛 2채널/4편 설명은 역사적 운영 설명이며 현재 생산 범위를 넓히지 않는다.

# Two-channel Shorts automation

Original daily commentary: 오늘의 오지랖 (Korean) and A Not-So-Polite Korean (English, one Korean's perspective). Two different Korean issues per daily batch; they may share a category. Each issue becomes one Korean commentary for 오늘의 오지랖 and one English explanatory adaptation for 동방예의없국 (A Not-So-Polite Korean): four separate videos, two per channel. English introduces unfamiliar Korean terms and context rather than mechanically translating the Korean script. Both versions share one verified factual source packet and retain consistent dates, quantities, attribution and uncertainty.

## Current state
The first eight videos, including the four November 5 stories, are public (see state/ledger.json). Both channels were verified by the YouTube API during today's uploads. Production is separately controlled by the repository variable PUBLIC_UPLOADS_ENABLED; check that variable and the latest Actions run for live state.

## Setup
1. Enable YouTube Data API in the dedicated Google Cloud project. Configure an external OAuth application and a Desktop client. Use personal-use production mode where allowed; Testing refresh tokens can expire after seven days.
2. Run connect_youtube.py with the downloaded client JSON on the owner's PC. Authorize youtube.upload and youtube.readonly separately for each channel. The helper verifies each channel ID and stores credentials with Windows DPAPI.
3. Save OPENAI_API_KEY, ELEVENLABS_API_KEY, YOUTUBE_OJIRAP_OAUTH and YOUTUBE_KOREAN_OAUTH in this repository's GitHub Actions Secrets. OAuth values are JSON with channel_id, client_id, client_secret and refresh_token. Never commit those values.
4. Manually run Daily Shorts in preflight mode. This checks connections without paid generation or upload. Only after it passes set repository variable PUBLIC_UPLOADS_ENABLED=true. Disable the previous local render-only automation at cutover.

## Schedule and limits
The UTC schedule 23:00 corresponds to 08:00 Asia/Seoul. GitHub may delay scheduled runs. One concurrent producer; maximum four videos and 6000 ElevenLabs narration characters per daily batch. OpenAI uses research, at most one completed-research repair, drafting, at most one narration-length correction, and one review per video (eight requests maximum, no retry loops). Research, drafting and reviews use gpt-5.4; reviews receive decoded, size-bounded actual photographs. Included voice credits are checked before production. A structured per-story source manifest is validated before drafting; an incomplete packet may receive one checkpointed repair that replaces unready events and is checked against actual source evidence from both research calls. If it remains invalid, drafting does not run. The draft inherits its exact bibliography and citation indices are remapped without inventing sources.

## Editorial and media
Google Trends KR/US RSS and web research discover topics. Claims require current cross-checked original reporting. EDITORIAL.md defines conversational individual commentary, English-first international packaging and one synchronized caption layer. ElevenLabs v4 uses connected prose rather than separate sentence chunks. Commons searches retrieve actual named subjects with explicit permitted licenses; an independent vision and source review must approve at least two relevant images per story. Missing relevant material holds the batch. Generic stock is forbidden. Photos are labeled archival and attributed with source, author, license and resizing/captioning notice. Automatic media discovery currently covers photos; TikTok/Reels discovery is not permission to republish video.

## Recovery and measurement
state/ledger.json records submissions before paid generation and uploads. Ambiguous or interrupted submissions stop for reconciliation; do not clear their markers and rerun blindly. Completed days and published job IDs never upload twice. GitHub Actions artifacts retain produced media for seven days. Public view, like and comment snapshots record video age and category; compare within the same channel at similar ages. Retention, engaged views and revenue need a separate YouTube Analytics connection.

## Validation
Run `python -m unittest discover -s tests -v`. Tests cover two-issue/four-job pairing, shared facts, language-scoped media, historical recovery, duplicate prevention, missing credentials, channel identity, interrupted requests, media ownership/relevance gates and caption continuity. A cloud preflight has passed; a successful preflight does not prove a future scheduled production batch will pass editorial review or upload successfully.



## Paired issue contract (from the next uncompleted daily batch)
Research returns two shared issue packets. Drafting expands each packet into exactly one `ojirap` and one `korean` job with the same canonical topic key and category, but unique job IDs and separate scripts/audio/video. Source binding attaches the exact same factual packet and hash to both adaptations. English background claims require packet citations; no all-Koreans claim or fixed catchphrase is required.

Deterministic checks enforce pairing, factual-packet identity, bibliography, same-channel recent-topic protection, job identity, channel-scoped media and the existing 6000-character cap. Four independent per-video reviews also see the counterpart and shared facts, and can reject inconsistent narration, unsupported background or mechanical translation. This is a review gate, not a guarantee of perfect semantic verification. The two pinned archived batches retain a content-hash-bound recovery path; arbitrary new four-topic batches cannot opt out.

`Shorts offline guards` runs tests on the exact pushed commit with no production credentials or external provider calls. Passing it does not mean a new video was generated or published. Completed production dates return before preflight, metrics, research, TTS, rendering or upload.

Unattended daily candidates are limited to routine low-impact public commentary. New serious allegations, sensitive personal facts or uncertain high-impact scope must fail review with `approval_required`; factual corroboration is not publication permission. Existing separately authorized additional-batch approvals remain unchanged.
