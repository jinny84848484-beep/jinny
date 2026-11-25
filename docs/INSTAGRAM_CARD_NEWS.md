# 주말 사용설명서(@weekend.manual) 카드뉴스 자동 게시

CEO 지시 2026-11-25: 인스타 카드뉴스 자동 업로드. 상시 승인(11/16) 범위 안에서 사람 대기 없이 돌아갑니다.

## 매주 흐름
| 언제(KST) | 누가 | 무엇 |
|---|---|---|
| 목 20:00까지 | 수석전략실장(dot) | `instagram/issues/<금요일 날짜>/`에 `issue.json` + `01.jpg`~ 커밋 |
| 목 21:37 | 수석비서실장(Claude) 예약 검수 | 필요하면 같은 폴더에 `review.json`(PASS/FAIL, 뺄 카드). 없으면 dot 자체 검수로 게시 |
| 금 12:37 (백업 14:07·18:07·토 08:37) | GitHub Actions `instagram.yml` | 검사 → 계정 확인 → 중복 확인 → 캐러셀 게시 → `instagram/posted.json` 기록 |

게시 가능 시간: 금 12:30 ~ 토 12:30. 그 뒤에는 지난 주말 정보라 올리지 않습니다.

## issue.json
```json
{
  "day": "2026-11-27",
  "self_check": "pass",
  "caption": "이번 주말 뭐 하지?…\n\n출처: …",
  "sources": ["https://…", "https://…"],
  "ai_generated": false,
  "cards": [{"file": "01.jpg", "alt": "표지: 이번 주말 갈 곳 4"}, {"file": "02.jpg", "alt": "…"}]
}
```
- 카드: JPEG만(PNG 불가), 2~20장, 8MB 이하, 비율 4:5~1.91:1, **모든 카드 같은 비율**(1080×1350 권장).
- 캡션: 2,200자·해시태그 30개·@ 20개 이하, `출처` 줄 필수. 끝에 완료 코드 `WM-YYYYMMDD`가 자동으로 붙습니다(중복 게시 방지).
- 출처 https 2곳 이상. AI 생성 이미지가 있으면 `"ai_generated": true`(인스타 AI 표시).

## review.json (선택)
```json
{"verdict": "PASS", "exclude": ["03.jpg"], "note": "3번 영업시간 미확인"}
```
`FAIL`이면 게시하지 않습니다(경고만, CEO 알람 아님).

## 알람 기준
- **실행 실패(빨간 X) = CEO 알람**: 토큰 없음·만료·다른 계정, 토큰 발급 50일 경과.
- **경고(held)**: 형식 오류·검수 FAIL·출처 부족 → dot가 고치고 다음 백업 실행이 게시.

## 처음 1회 설정 (CEO)
1. Meta 개발자 앱(무료)에 "Instagram API with Instagram Login" 추가, 권한 `instagram_business_basic`, `instagram_business_content_publish`.
2. @weekend.manual로 로그인해 장기 토큰(60일) 발급.
3. GitHub → Settings → Secrets: `IG_ACCESS_TOKEN`, `IG_USER_ID` / Variables: `IG_TOKEN_ISSUED_ON`(발급일).
4. 50일마다 토큰 갱신 후 두 값 업데이트(실행이 10일 전에 알람).
