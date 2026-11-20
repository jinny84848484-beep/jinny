# 방구석 세계요리 19:00 예약 인계 (운영 미연결)

`python -m world_kitchen.release_runner --repo-root . --output-root runs/world_kitchen --date YYYY-MM-DD`는 기존 레시피 큐와 오프라인 렌더 결과만 읽는다. 기본 날짜는 KST 현지 날짜다. 오늘 큐가 비면 `no_ready_episode`, 첫 검수 전이면 `awaiting_recipe_and_rights_review`. 날짜 중복, 렌더 누락, 파일 교체, 채널 불일치 등은 `held`와 exit code 2로 표시한다.

검사 범위는 렌더러가 쓰는 해당 날짜의 `video.mp4`, `result.json`, `description.txt`, `english.srt`로 제한된다. MP4 해시와 렌더 메타데이터, 세로 H.264/AAC 및 길이, 원본 설명 및 영어 자막 일치를 검사한다. 아무 승인 파일을 읽어서 발행 준비로 간주하지 않는다. 영어 SRT는 검토 파일이고 YouTube 추가 트랙을 올리지 않는다.

## 운영 연결 전에 할 일

- CLI에는 실발행 모드, API 호출, 자격증명 로드가 없다. 기본 `run_release`도 예약·원장·전송 어댑터를 호출하지 않는다. `load_trusted`로 검수 증빙을 검증해도 기본값은 모의 검증뿐이다.
- 운영자가 검증한 별도 진입점에서만 `load_trusted(candidate)`와 `ReleaseAdapter`를 결합한다. 보호된 승인 저장소는 렌더러 원본·MP4·SRT 지문, 최초 안전/권리 검수, 최종 화면/음성/자막·기존 게시 문구 승인을 검증한다. 런타임 제출값, Actions 캐시, 자기 선언 승인 JSON은 근거로 쓰지 않는다.
- 그 이후에도 `run_release(..., live=True, adapter=..., load_trusted=...)`를 호출하려면 인증된 월드키친 채널·영구 공용 CAS·정확한 소유자 조회·권리/승인 유효성·API 시한/무재시도를 실제로 검증해야 한다. 어댑터 스텁, 테스트 더블만 제공하며 실제 네트워크 어댑터는 운영 준비가 아니다.
- 승인된 대상의 비공개 업로드 후 알려진 ID가 처리 중이면 `publication.resume_known_private`로 원장 기반 읽기 재확인만 이어간다. 불명확한 업로드나 예약 요청은 절대 재호출하지 않는다.
- 19:00 KST 예약 확인과 실제 공개 확인은 별개다. 기존 공개 영상을 큐에 재입력하지 않는다.

옵션 수동 GitHub workflow는 cron·YouTube 권한·서비스 비밀값 없이 가짜 로컬 자산 테스트와 큐 조회만 수행한다. 별도 workflow로 새 runner를 실행하므로, 17시 workflow의 로컬 파일이 자동 공유되지 않는다. 현재 빈 큐에는 정상적으로 부작용 없이 종료하며, 큐에 항목이 있는데 결과물이 체크아웃에 없으면 실패를 숨기지 않는다. 실제 렌더 결과는 승인된 작업환경에서 `--output-root`로 지정한다. 현행 17시 렌더 워크플로와 19시 목표 시각은 변경하지 않았다.

## Journal/transport composition
`CookingJournal` validates a dedicated private day ledger and binds each
write to the current atomic snapshot revision. `JournaledReleaseAdapter`
passes a transport request only after checking current approval and consuming
the matching durable intent once. It can be passed to `run_release`; a
separately authenticated scheduler can use `resume_known_private` with only
known unarmed private IDs. In-memory tests run without any provider access.

Wire `open_snapshot` and `compare_and_set` to the same private durable shared
store for all managed writers. Do not store reservation or private video IDs
in the public repository or run artifacts. No automatic replay on network or
CAS uncertainty. A live transport must still verify the authenticated YouTube
owner/channel, exact immutable upload, current permissions, time deadline and
known private readbacks; the draft dry-run workflow never supplies it.
