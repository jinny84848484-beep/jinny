# 2026-11-17 운영 연결 점검

## 실제 확인된 완료
- 11/16 퀘사디아 UQNDeho8LxI 및 11/17 카프레제 MlM-NJYHBkE는 운영자가 YouTube Studio에서 공개 상태를 확인했다. 이는 수동 게시이며 자동 파이프라인의 성공이 아니다.
- 전용 production-state.json에 두 날짜·요리·영상ID를 기록하여 동일 날짜 재생성/재게시 및 최근 요리 중복을 막는다. 이 파일을 main에 배포할 때 현재 운영 원장과 반드시 대조해야 한다. 배포 후 파일을 빈 상태로 초기화하지 않는다.
- Anna Kim 승인 ID uyVNoMrnUku1dZyVEXwD, 이름 Anna Kim - Tender, Calm and Clear로 신규 원고213자 TTS 1회 성공(기존 포함 이용량, 추가구독 없음). 음성32.496초 및 raw alignment 원문 일치, 최종 영상33초 확인. 계정의 할당량 수치는 저장소에 기록하지 않는다.
- preflight가 저장된 음성 목록에만 의존하지 않고 공식 GET /v1/voices/{voice_id}를 호출해 승인 ID/전체 표시이름의 완전 일치를 확인하도록 변경. GET은 음성 import·생성·구독 변경이 아니다. API 오류/잘못된 ID/이름은 차단하며 임의 다른 목소리로 대체하지 않는다.
- 직접 청취 검수는 수행하지 못했다. 기술검사·alignment 일치를 인간의 귀로 들은 검수로 주장하지 않는다.

## 남은 운영 차단
1. PR #1은 draft이며 main에 미병합. main 기존 경로는 빈 검토 큐를 읽는 렌더러로, 일일 자동 생성·게시가 아니다.
2. YOUTUBE_KITCHEN_OAUTH가 없음을 운영 설정 점검에서 확인했다. 다른 채널 OAuth를 바꾸어 쓰거나 Studio 세션/쿠키를 추출하지 않는다. 두 개인PC는 11/17 점검 시 오프라인이었다.
3. 전용 OAuth는 기존 Google 프로젝트의 승인된 OAuth client와 정확 요리 채널의 공식 인증·동의 흐름으로 연결해야 한다. 저장 전 channel_id=UCkuz6ra5_hZ1MSAbFthqDcg와 실제 mine 채널 조회, 자막 편집 scope를 확인한다. 기존 비밀값을 문서/채팅/로그로 출력하지 않는다. 별도 인증 또는 확장 권한 동의가 필요한 경우 해당 승인 흐름을 따른다.
4. GitHub runner에 기존 브랜드 워드마크/BGM 파일이 배포되어 있지 않다. 로컬 파일 경로 설정만으로 해결되지 않는다. 검증된 실제 바이너리와 출처/표기를 함께 배포하고 존재·해시를 검증해야 한다.
5. OpenAI는 오늘 뉴스 생성 호출에서429를 반환했다. 단순 models 목록 조회 성공은 유료 생성 또는 포함 할당량 상태 증거가 아니다. 결제 변경 없이 원인·사용가능량 확인이 필요하다. 자동 재시도로 누적비용을 발생시키지 않는다.
6. KITCHEN_USAGE_APPROVED/GENERATION_ENABLED/PUBLISH_ENABLED는 필요한 연결과 검증 전 켜지 않는다. secrets/vars를 이 변경에서 생성하거나 확장하지 않았다.

## 안전한 적용 순서
오프라인 테스트 → 동일 commit CI/독립 리뷰 → 전용OAuth와 자산 연결 → 권리/할당량 확인 → 읽기 전용 publish preflight → 원장 대조 → 승인된 main 배포 → 다음 날짜 1편만 생성/검수/비공개 업로드/영어자막 serving/19시예약 readback.
오늘 영상을 시험용으로 다시 생성/업로드하지 않는다. 운영 연결이 미완료면 19시 자동 게시 성공을 약속하지 말고 기존 수동 제작·Studio 게시 대안으로 결과를 보완한다.

공식 근거:
- https://elevenlabs.io/docs/api-reference/voices/get
- https://developers.google.com/youtube/data/guides/authentication

추가 접근 점검: 공식 Google Cloud Credentials 페이지를 클라우드 브라우저에서 열고 한 번 재조회했으나 Site Unavailable / Unable to access this site. 표시로 진행 불가. 기존 OAuth client 존재와 공식 동의 화면은 확인하지 못했다. 자격증명/세션 추출, 사용자 비밀번호 요청, 우회는 하지 않았다.
