# 방구석 세계요리 — 매일 자동 제작·예약 게시

CEO 지시 2026-11-16(채널 규칙)·2026-11-25(자동화 구축). 사람 확인 단계 없이 하루 1편을 만들어 비공개로 올리고 19:00 KST에 공개되도록 예약합니다.

## 파일 지도
| 파일 | 하는 일 |
|---|---|
| `daily.py` | **하루 실행 순서**(시작점). `python -m world_kitchen.daily` |
| `episode.py` | 영상 내용: 기획·검수 프롬프트, 기획 → 렌더 입력 변환, 음성 길이 맞추기, 로고, 19시 예약 시각 |
| `providers.py` | 외부 연결 전부: OpenAI(글·이미지), ElevenLabs(음성), YouTube(채널 확인·중복 확인·예약 업로드) |
| `ledger.py` | 기록: `world_kitchen/ledger.json`(날짜별 상태·완료 코드 `WK-YYYYMMDD`) |
| `pipeline.py` | 렌더러 + 규칙 검사(장면 순서, 700W·1000W 시간 표시, 끝 인사) |
| `template.json` | 화면 배치·색·글꼴·끝 인사 |
| `../kitchen_guard.py` | 조리 안전 검사(전자레인지·밥솥만, 계량 단위, 금지 조리법, AI 표시) |
| `runner.py`, `queue.json` | 옛 수동 경로(검수된 입력만 렌더). 자동 실행에는 쓰지 않음 |

## 하루 흐름 (`.github/workflows/world-kitchen.yml`, 10:07 · 12:37 · 15:07 · 17:00 KST)
1. 기획: 웹에서 레시피 출처 2곳 이상 확인, 최근 30일 요리와 겹치지 않게
2. 독립 검수: 출처·가열 시간·안전 재확인 (FAIL이면 그날 보류)
3. 규칙 검사: `pipeline.validate` + `kitchen_guard`
4. 이미지: 장면별 AI 음식 사진(글자·상표·얼굴 없음), 끝 장면은 완성 사진 재사용
5. 음성: 장면별 ElevenLabs → 장면 길이를 음성에 맞춤
6. 렌더 → 비공개 업로드 + 19:00 공개 예약(17:00 이후 늦게 돌면 업로드 20분 뒤 공개)
7. `ledger.json`에 완료 기록

## 알람 기준
- **실행 실패(빨간 X) = CEO 알람**: 키·OAuth 없음/만료/다른 채널, ElevenLabs 잔량 1,200자 미만, OpenAI 크레딧 부족.
- **경고(held) = 내용 문제**: 검수 FAIL·규칙 위반 → 다음 실행이 1번 더 시도(하루 최대 2번).
- 업로드 도중 끊기면 채널에서 `WK-YYYYMMDD`를 찾아 기록만 하고, 못 찾으면 멈춥니다(중복 업로드 방지).

## 처음 1회 설정 (CEO)
- GitHub Secret `YOUTUBE_KITCHEN_OAUTH`: 방구석 세계요리 채널 OAuth(JSON: channel_id, client_id, client_secret, refresh_token). 오지랖 채널(`YOUTUBE_OJIRAP_OAUTH`)과 같은 형식·같은 Google OAuth 클라이언트로 발급(권한: youtube.upload, youtube.readonly). 계정 로그인은 CEO가 직접.
- GitHub Variable `KITCHEN_VOICE_ID`: 채널에 쓰는 ElevenLabs 목소리 ID.
- 비용: 하루 이미지 5~6장(OpenAI) + 음성 약 300자 + 글 생성 2회. [금액 미검증]

## 검사
`python -m unittest tests.test_world_kitchen tests.test_world_kitchen_daily` (네트워크 없음)
