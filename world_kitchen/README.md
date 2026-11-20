# 방구석 세계요리 자동 제작 경로 v1

## 구현 범위
`python -m world_kitchen.runner`는 검토된 요리 큐를 읽어 **고정 양식 영상 + 선택형 영어 SRT + 설명 + 검수 결과 파일**을 자동 제작한다. 뉴스/코기 제작 코드와 OAuth·원장을 공유하지 않는다. `.github/workflows/world-kitchen.yml`은 매일 17:00 KST 또는 수동 실행으로 준비된 입력을 렌더하며, 목표 게시 시간은 19:00 KST 메타데이터다.

## 종단간 경로 (코드 구현, 실서비스 미검증)
`python -m world_kitchen.automatic`은 아래 단계를 순서대로 구현한다.
1. 실제 웹 검색 근거를 갖춘 요리/트렌드 조사. 최근 제작 요리 제외.
2. 동일 manifest에 수석비서실장 deterministic guard와 별도 AI 출처/안전 검수.
3. 장면별 AI 이미지 최대 8개. 원본 비율 유지. 추가 구매 API 없음.
4. Anna Kim ID 확인, 포함 크레딧 충분한 경우만 ElevenLabs 1회(최대 1,200자). 타임스탬프로 장면 길이를 결정.
5. 실제 생성 음성의 ASR 전사와 고정 템플릿 렌더. 최종 화면/영어/숫자와 전사를 별도 AI 검수.
6. 정확 채널·기존 예약/공개 영상 중복 조회 → 비공개 업로드 → 영어 자막 업로드 → 처리완료 시 19시 예약 → 영상·자막 serving 상태 readback.

수동 제작 첫 영상은 다시 생성/업로드하지 않는다. `production-state.json`은 뉴스 원장과 분리된 GitHub CAS 영구 원장이다. 각 유료 호출/게시 전에 상태가 저장되어야 하며 불확실한 제출은 자동 반복하지 않는다. 자막 실패는 비공개 유지, 처리완료/검수 실패 시 공개 예약하지 않는다. 미처리/실패를 다시 실행하여 지우지 말고 결과를 대조해야 한다. 업로드/자막 반환 ID는 원장 후속 저장 전 로컬 receipt에도 기록한다. 후속 CAS 저장 실패 시 해당 artifact와 실제 채널을 대조하고, submitted 원장을 지우거나 재업로드하지 않는다. 자동 검수는 사람의 청취나 실제 조리 실험을 의미하지 않는다.

### 활성화 전 필요한 실제 연결
- 기존 `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`를 **이름으로만** workflow에 연결했다. 실제 저장 여부/계정 상태는 이 작업 환경에서 확인하지 못했다.
- `KITCHEN_VOICE_ID`: 실제 승인된 Anna Kim 목소리의 ID. 다른 목소리로 자동 대체하지 않는다.
- `KITCHEN_LOGO_PATH`: 검토된 wordmark PNG가 GitHub runner에서 존재해야 한다. 로컬 제작 패키지의 banner.png를 가리키는 로컬 경로는 CI 파일이 아니다. 현재 repo에 브랜드 바이너리는 없음.
- `KITCHEN_MUSIC_PATH`, `KITCHEN_MUSIC_RIGHTS_URL`, `KITCHEN_MUSIC_CREDIT`: 승인된 BGM 파일·사용권 근거·필수 저작자 표기가 모두 있어야 한다. 웹에서 임의 음악을 다운로드하지 않는다.
- `YOUTUBE_KITCHEN_OAUTH`: 요리 채널 전용 OAuth JSON. 기존 뉴스/삭제 코기 OAuth를 재사용하지 않는다. 영어 자막 때문에 `youtube.force-ssl` 권한이 필요하며, refresh 응답의 scope로 확인되지 않으면 차단한다. 권한을 자동 확장하지 않는다.
- `KITCHEN_USAGE_APPROVED=true`는 기존 API 사용량 범위를 별도 확인했음을 뜻하며 결제/구독 허가가 아니다. 생성은 `KITCHEN_GENERATION_ENABLED=true`, 게시까지는 `KITCHEN_PUBLISH_ENABLED=true`가 각각 필요하다. 이 작업은 해당 변수를 켜지 않았다.
- 로컬에서 실제 preflight는 키/목소리ID 미설정으로 요청 전에 차단됐다. GitHub secret 이름 존재를 확인하는 관리 도구는 현재 connector에 없고, 공개 workflow의 참조는 secret 존재 증거가 아니다.
- Actions 수동 실행의 기본 `mode=preflight`는 조회만 하고 생성·업로드하지 않는다. 실제 통과 후에만 `produce`와 설정값으로 운영한다. 별도 자격증명이나 권한 생성은 소유자의 승인/인증 절차를 따른다.

**코드 구현과 안전 mock 테스트 통과는 실제 매일 자동 게시 성공이 아니다.** 실서비스 연결과 첫 실제 production run은 아직 미검증이다. 기본 변수는 미활성 상태이므로 검토 큐가 비어 있으면 `no_ready_episode`로 종료한다.

## 공동 검수 통합

수석비서실장이 추가한 `kitchen_guard.validate_recipe`를 동일 manifest의 재료·단계·화면·음성에 호출한다. 별도 복사본을 검증하지 않는다. 최신 사용자 지시의 치즈 28그램을 지원하도록 기존 가드에 g/그램 단위와 별도 질량 범위를 추가했다. 기존 9개 가드 테스트는 그대로 유지한다. 정규식 가드는 실제 조리 안전 심사를 대체하지 않는다.

## 고정 구조
- 첫 장면 완성 요리, 짧은 후킹 문구. **필수 재료 수에 맞는 `재료 딱 N개!`를 첫 화면에 표시**하며 누락/허위 개수는 자동 차단한다. 모차렐라 등 optional:true 추가는 기본 재료 수와 구분한다. 브랜드 인트로로 지연시키지 않는다.
- 흰/크림 상단에 작은 기존 로고, 중앙에 **원본 비율을 유지한 음식 전체**, 하단에 짧은 설명.
- 재료 → 조리 단계 → 700W·1000W별 **근거 있는 시간** → 식힘/완성 → 고정 시그니처.
- 화면당 한 행동. 글귀가 바뀌면 대응 이미지도 변경한다. 같은 이미지를 꼭 재사용해야 하면 검토 이유를 남긴다.
- 한국어는 영상에, 영어는 선택형 SRT로 생성. 영어 SRT 파일 생성은 YouTube 트랙 업로드 성공과 다르다.
- 기존 ElevenLabs 목소리로 밝고 섬세하며 명확하게. `28, 그램`처럼 숫자와 단위가 끊긴 문장은 차단한다. 청취 검수는 별도 필수다.
- 끝 인사: `오늘도 방구석에서 세계 한 입, 맛있게 드세요!`
- 모차렐라가 늘어나는 컷은 선택 추가 결과임을 표시한다. AI 정지 이미지를 실제 조리영상이라고 주장하지 않는다.

## 실행
1. 이 폴더 `episode.example.json`을 복사하고 요리별 재료·양·단계·출처·시간·영어 자막을 작성한다. 출력별 시간은 요리/분량/상태와 맞는 근거를 사용하며 다른 요리에 45/30초를 복사하지 않는다.
2. 권리 확인된 이미지, 로고/워드마크 PNG, 시간 정렬된 ElevenLabs 음성 파일을 실행 환경에 준비한다. 저장소에는 비밀키, 비공개 사용량 내역, 계정 파일을 넣지 않는다. 파일 경로는 manifest에서 지정한다.
3. `python -m world_kitchen.pipeline episode.json`으로 검증한다.
4. `python -m world_kitchen.pipeline episode.json --output runs/world_kitchen/review-name`으로 로컬 렌더한다. 기존 출력 디렉터리는 덮어쓰지 않는다.
5. 준비된 파일이 GitHub runner에서도 접근 가능한 경우에만 queue.json에 `{"date":"YYYY-MM-DD","reviewed":true,"manifest":"world_kitchen/episodes/episode.json"}`을 추가한다. 로컬 경로만으로 CI에서 자산이 있다고 가정하지 않는다.
6. `python -m world_kitchen.runner --date YYYY-MM-DD`가 큐에서 1편을 렌더한다. 실패나 시도 이력이 있으면 자동 반복하지 않는다.
7. 실제 화면·음성 청취·영문 동기화·안전·권리·채널·중복을 검수한 뒤 별도의 승인된 게시 경로를 사용한다.

큐 및 결과는 제목·설명·자막·음성의 숫자 일치와 사람의 의미 검수까지 보장하지 않는다. API 자격증명/사용량·권한/자산 확인과 preflight 없이 게시 변수를 켜지 않는다. 기본 상태의 두 자동화 플래그 false는 완전 자동 생성/업로드 실서비스 미연결을 뜻하며 검토 입력 렌더 workflow의 실행 여부와 다르다.

## 실패·중복 처리
GitHub concurrency 그룹은 요리 전용 단일 실행이다. 로컬 상태에는 렌더 시작을 먼저 기록하고 애매한 실패는 자동 재시도하지 않는다. workflow는 상태를 캐시에 보존하지만 캐시는 영구 DB가 아니므로 유실될 수 있다. **검토 큐의 이 캐시는 업로드 중복 방지 원장이 아니다.** 종단간 경로는 별도의 `production-state.json` CAS 원장과 채널 inventory를 사용한다.

## 검수·재현
- `python -m unittest discover -s tests -p test_world_kitchen.py -v`
- `python tests/run_offline.py` (네트워크와 프로덕션 비밀키 없는 전체 테스트)
- 코드·테스트 통과, 렌더 산출물, 실제 게시 성공을 각각 구분해서 보고한다.
- 수석비서실장 코드 검토: 채널 격리, 레시피별 시간 근거, 레이아웃/자막 안전영역, 재시도·중복, 자격증명 미포함, 위 미연결 항목을 확인한다.

## API 검토 근거
- OpenAI Images: https://developers.openai.com/api/reference/resources/images/methods/generate
- ElevenLabs timestamps: https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- YouTube scheduling: https://developers.google.com/youtube/data/reference/rest/v3/videos
- Caption authorization: https://developers.google.com/youtube/data/reference/rest/v3/captions/insert

수석비서실장 추가 검토: recipe guard 연동, 단계별 CAS 선기록, uncertain submission 후 무재시도, caption 실패 시 비공개, live preflight 권한·사용량 검증, 실제 영문 자막 serving 확인을 중심으로 검증 요청.

### Journal write permission
Workflow defaults to `contents: read`; only the cooking production job has `contents: write` because it must commit the compare-and-swap journal BEFORE every external submission, then persist the returned receipt. Splitting this into a later write-only job would lose that pre-submission durability. This is not deployment/admin permission; no credentials or authority were added. The token remains job-scoped (not file-scoped), a residual broad-repository-write risk for reviewer approval. Production and publishing gates remain disabled until separately verified.
