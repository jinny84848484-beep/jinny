# Two-channel Shorts automation

Original daily commentary: 오늘의 오지랖 (Korean) and A Not-So-Polite Korean (English, one Korean's perspective). Four different topics and categories per batch, two videos per channel.

## Current state
The first four videos are public (see state/ledger.json). Cloud production is disabled until OAuth credentials and Actions secrets pass preflight. A repository or workflow alone is not an active connection.

## Setup
1. Enable YouTube Data API in the dedicated Google Cloud project. Configure an external OAuth application and a Desktop client. Use personal-use production mode where allowed; Testing refresh tokens can expire after seven days.
2. Run connect_youtube.py with the downloaded client JSON on the owner's PC. Authorize youtube.upload and youtube.readonly separately for each channel. The helper verifies each channel ID and stores credentials with Windows DPAPI.
3. Save OPENAI_API_KEY, ELEVENLABS_API_KEY, YOUTUBE_OJIRAP_OAUTH and YOUTUBE_KOREAN_OAUTH in this repository's GitHub Actions Secrets. OAuth values are JSON with channel_id, client_id, client_secret and refresh_token. Never commit those values.
4. Manually run Daily Shorts in preflight mode. This checks connections without paid generation or upload. Only after it passes set repository variable PUBLIC_UPLOADS_ENABLED=true. Disable the previous local render-only automation at cutover.

## Schedule and limits
The UTC schedule 23:00 corresponds to 08:00 Asia/Seoul. GitHub may delay scheduled runs. One concurrent producer; maximum four videos and 6000 ElevenLabs narration characters per daily batch. OpenAI uses at most three bounded requests for research, scripts and independent factual review. Insufficient included voice credits stop production.

## Editorial and media
Google Trends KR/US RSS and web research discover topics. Claims require current cross-checked original reporting. English scripts explain Korean context; opinions and satire are labeled. Only assets in assets/media-library.json may be used. Pexels illustration is labeled; TikTok/Reels discovery is not permission to republish footage. The model cannot choose arbitrary download URLs. Additional footage requires an editorially reviewed library entry.

## Recovery and measurement
state/ledger.json records submissions before paid generation and uploads. Ambiguous or interrupted submissions stop for reconciliation; do not clear their markers and rerun blindly. Completed days and published job IDs never upload twice. GitHub Actions artifacts retain produced media for seven days. Public view, like and comment snapshots record video age and category; compare within the same channel at similar ages. Retention, engaged views and revenue need a separate YouTube Analytics connection.

## Validation
Run `python -m unittest discover -s tests -v`. Tests cover topic diversity, duplicate prevention, missing credentials, channel identity and interrupted requests. Real cloud preflight and a first scheduled run remain required after credential setup.
