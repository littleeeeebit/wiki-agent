# 2단계 — 미러와 위키 산문

전체 설계와 세 단계의 관계는 [개요](0-overview.md)에 있다.

목표. 사용자 화면에 뜨는 것이 영어가 되고, 그 옆에 한국어 미러가 선다.

선행 조건. 1단계가 끝나 `tool/translate.py` 가 있어야 한다.

순서 규칙. `mirror.py` 가 먼저 돌아야 `english_progress.py` 를 켠다. 거꾸로
하면 사용자가 읽을 수단 없이 영어만 보는 구간이 생긴다.

## 만들 것

### `tool/mirror.py`

Claude Code와 Codex의 출력을 한국어로 옮겨 찍는다. mirror 담당자가 첫 작업으로
각 호스트의 수집 경로·세션 식별·표본을 확인하고 `--host claude|codex`로 구분한다.
아래 JSONL 명세는 Claude용이다. Codex는 실제 표본으로 읽기 구현과 검사를 정하고,
수집 경로가 없으면 mirror-live를 보류한다. 다른 셀의 기록을 대신 읽어 통과시키지 않는다.
Orca의 두 번째 셀은 사람이 열며 실행법을 `docs/mirror-setup.md`에 적는다.

- 로그 위치: `~/.claude/projects/<저장소 슬러그>/*.jsonl`.
  `tool/sessions.py` 가 같은 경로를 이미 안다 (`SESSIONS`, `sessions.folder`) —
  새로 쓰지 말고 그것을 부른다. 당시에는 `census.transcript_dir` 이었고,
  미러가 진단 도구를 임포트하게 만든 자리라 뒤에 떼어 냈다
- 인자 없이 돌리면 이 저장소 슬러그에서 `mtime` 이 가장 최근인 jsonl 을 잡는다.
  `--session <경로>` 로 지정할 수 있다
- 번역 대상:

| 대상 | 옮기나 |
| --- | --- |
| `assistant` 의 `text` 블록 | 옮긴다 |
| `assistant` 의 `tool_use` 중 `description` 필드 | 옮긴다 |
| `tool_use` 의 나머지 입력 (명령·파일 내용·패치) | 안 옮긴다. 코드다 |
| `tool_result` | 안 옮긴다. 양이 크고 대부분 코드다 |
| `user` (사람이 친 것) | 그대로 찍는다. 이미 한국어다 |
| `<system-reminder>`·주입문 | 안 찍는다. `census.INJECTED` 표지로 거른다 |

- 파일 끝을 폴링한다. 파일이 잘리거나 사라지면 다시 찾는다 (`/clear` 가 새 세션
  파일을 만든다). 기존 파일이 남은 채 새 파일이 생기는 경우도 재탐색한다.
  쓰는 중인 마지막 불완전 줄은 버리지 않고 다음 읽기까지 보관한다.
  여러 셀이 있으면 mtime만으로 대상이 확정되지 않으므로 `--session`을 명시한다
- 번역 실패는 원문(영어)을 찍는다. 미러가 비는 것보다 영어라도 보이는 것이 낫다
- `stdout` UTF-8 고정. 이건 파이프가 아니라 터미널이지만 `craft/hooks-fail-open` 이
  범위를 `tool/*.py` 전부라고 못 박았다

실행 방법은 수동이다. 셀 생성 자동화는 이 계획에 포함하지 않는다.

### 실측으로 바뀐 것 — 미러는 터미널이 아니라 화면이다

계획서가 쓴 대로 터미널 출력으로 먼저 만들었고 그 경로는 `--web` 없이 그대로 돈다.
그 위에 사용자 요청으로 로컬 화면을 얹었다. `tool/mirror.html` 한 장을 stdlib
`http.server` 가 `127.0.0.1:8788` (위키 채팅 `8787` 다음 번호, 막혀 있으면 올려 잡음)
에 띄우고 `/stream` 으로 SSE 를 흘린다. 새 의존성 0, 빌드 단계 0.

두 면이다. 왼쪽은 번역된 대화, 오른쪽은 번역 안 한 명령과 패치. 산문과 코드는
필요한 폭이 정반대라 한 열에 두면 둘 다 진다. 값과 근거는 루트 `DESIGN.md` 가 든다.

계획서 명세에서 실제로 틀렸던 것 여섯:

- manifest 의 kind 가 셋뿐이라 일부러 한국어로 남긴 문서를 적을 자리가 없었다.
  그래서 그 문서들이 manifest 에서 통째로 빠졌는데, 빠진 것과 잊은 것을 게이트는
  구별하지 못한다. 실제로 13개 파일이 보호 구간 대조를 한 번도 안 받은 채
  "게이트 초록" 으로 적혔다. `kept` 를 더하고 `why` 를 의무로 걸었다

- **Codex 세션은 `~/.codex/sessions` 한 곳에 없다.** `CODEX_HOME` 이 그 자리를
  옮기고, Orca 는 그것을 계정마다 따로 준다 — 실제 경로는
  `%APPDATA%/orca/codex-accounts/<id>/home/sessions` 다. 기본 경로만 읽는 미러는
  사용자가 실제로 쓰는 세션을 한 번도 못 본 채 "세션 없음"이라고 말한다.
  2걸음의 Codex 쪽이 막혀 있던 이유가 파서가 아니라 이것이었다.
  `codex_homes()` 가 셋을 다 모은다. 저장소 목록이 3개에서 11개가 됐다

- Claude 의 중간 발화는 `user` 레코드가 아니다. `queue-operation`/`enqueue` 의
  `content` 에만 원문이 남는다. 이것을 안 읽으면 사람 발화 11건 중 1건만 뜬다
- `HTTPServer.allow_reuse_address` 가 Windows 에서는 남의 포트를 뺏는다.
  끄지 않으면 포트 넘기기 고리가 안 돌고 두 미러가 같은 포트를 서빙한다
- 두 호스트의 `timestamp` 는 UTC 다. `transcript.py` 처럼 잘라 쓰면 9시간 틀린다
- Codex 의 도구 설명은 `McpToolCall.arguments.title`, 패치는 `FileChange` 의
  `unified_diff`, 명령은 `CommandExecution.command` 다

### `tool/test_mirror.py`

- 가짜 jsonl 을 만들어 `tool_result` 와 주입문이 안 찍히는지
- 번역 함수를 가짜로 바꿔 `assistant` 텍스트와 `description` 만 통과하는지
- 파일이 중간에 잘렸을 때 죽지 않는지

## 고칠 것

### 강제 뒤집기 — `korean_progress.py` → `english_progress.py`

`mirror.py` 가 실제로 도는 것을 확인한 뒤에 한다.

- 판정을 뒤집는다: 설명의 보호 구간 밖에 한글이 있으면 deny.
  용어집의 `keep_korean` 및 백틱 속 명령·경로는 허용한다
- 사용자에게 보이는 차단 사유와 `systemMessage`는 한국어로 유지한다.
  `WATCHED = {"Bash", "Agent", "Task"}` 는 그대로
- 예외는 그대로 통과 — 진입점 가드, `stdin`/`stdout` UTF-8 고정
- 파일 이름이 바뀌므로 배선이 따라와야 한다. `.claude/settings.json` 의
  `PreToolUse` 항목은 `tool/apply.py` 가 페이지 front matter 의
  `enforce: pretooluse:` 에서 생성한다. 페이지를 옮기고 `apply --write` 를 돌린다
- Codex는 `codex_pretool.py`가 `korean_progress`를 직접 import하고 호출한다.
  import와 호출을 같이 바꾼다. Claude의 `apply.merge/put_hook`는 옛 훅을 지우지 않으므로
  소유한 기존 `korean_progress.py` 엔트리만 제거하는 마이그레이션을 추가한다.
  사용자 훅은 보존한다. 기존 설치를 입력으로 한 업그레이드 및 두 번 적용 검사를 둔다

### 페이지 옮기기 — `operator/korean-progress.md` → `operator/english-progress.md`

- 본문을 영어로 다시 쓴다. 규칙이 뒤집혔으므로 번역이 아니라 재작성이다
- `triggers` 는 한국어 정규식 그대로 두되, 뒤집힌 규칙에 맞게 다시 고른다
- `links` 와, 이 페이지를 가리키는 다른 페이지의 `[[korean-progress]]` 를 전부 고친다.
  `operator/ask-with-arrow-key-options.md`, `operator/report-without-stopping.md`,
  `craft/hooks-fail-open.md`의 links와 `index.md`의 Markdown 링크도 가리킨다.
  `rg`로 코드·테스트·문서의
  옛 이름 참조도 전수 확인하고 동작 검사 변경은 3단계로 미루지 않는다
- 전수 조사에서 실제로 나온 나머지 둘: `README.md:24` 의 `tool/` 목록에 적힌
  `korean_progress`, 그리고 `tool/test_apply.py:43` 의
  `SCRIPTS = {"korean_progress.py": ...}`. 뒤엣것은 이름을 안 고치면 테스트가
  옛 이름으로 배선을 검증해 초록이 난다 — 배선이 바뀐 것을 검사가 못 본다
- 프로젝트 지식인 `.wiki/graph.json`·`.wiki/corpus.json`과 허브 정책 지도인
  루트 `graph.json`은 별개다. 전자는 `python tool/sync.py --project .`, 후자는
  `python tool/graph.py`로 재생성하고 `/api/graph`에서 옛 페이지 ID가 없는지 확인한다

### 웹 문서·지도 표시 — pages 전 선행 작업

2단계 웹 표시 담당자가 `POST /api/translate`와 `Peek.tsx`의 한국어 문서 표시를
구현한다. API는 1단계 번역기를 호출하며 방향·길이를 검증하고 localhost 바인딩을 유지한다.
원문 경로·줄번호와 실제 원문은 보존하고 번역을 별도 표시한다. 지도
`web/src/graph/force.ts`의 제목·규칙 설명도 같은 API로 한국어를 보여 준다.
식별자·링크·설정 값은 번역하지 않는다. 실패는 원문과 한국어 실패 안내를 보여 준다.
가짜 영어 문서·지도 응답으로 먼저 검증하고 그 뒤 실제 페이지를 바꾼다.
이 최소 표시 경로는 3단계를 기다리지 않고 2단계에서 출하한다.

`graph.load_pages()`도 `규칙.`만 읽으므로 `Rule.` 문단을 인식하게 한다.
한·영 표본에서 rule이 비지 않는 검사와 지도 한국어 표시 검사를 이 단계에 넣는다.

### 위키 산문 영어화 — 32개 파일

`operator/` 9 · `craft/` 12 · `docs/` 6 · 루트 5 (`README`·`SCHEMA`·`ENFORCEMENT`·
`MAINTENANCE`·`index`). 새 `docs/mirror-setup.md`는 이 32개에 별도로 추가한다.
`docs/plans/` 네 문서는 한국어로 유지한다.

#### 실측으로 늘어난 것과, 사용자가 뺀 것

대상이 32개가 아니라 규칙 24장 + 루트 5장 + `docs/` 5장 = 34장이었다.
계획서를 쓴 뒤 `craft/` 가 12 → 15 로 늘었고 루트에 `DESIGN.md` 가 생겼다.

`docs/chat-setup.md` 와 `docs/mirror-setup.md` 는 **한국어로 남긴다.** 사용자가
2026-09-22 에 그렇게 정했고, 근거는 계획서 0번의 기준 자체다 — 영어로 가는 것은
"에이전트가 닿는 표면" 인데 이 둘은 팀원이 설치하기 전에 읽는 문서다. 설치 전에는
미러도 인용 서랍도 안 도니 한국어로 읽을 경로가 없고, 그러면 이 계획의 순서 규칙
("한국어로 읽을 경로가 서기 전에는 사용자 화면을 영어로 바꾸지 않는다") 을 정면으로
어긴다. 두 파일은 `operator/english-progress` 가 말하는 "사람이 읽는 것" 쪽이다.

각 파일에서 건드리지 않는 것:

- front matter 전체. 특히 `triggers` — 사용자 발화를 매칭한다.
  단 위 페이지 재작성의 enforce·links·trigger 변경은 manifest의 정확한 허용 차이로 기록한다
- 백틱 안, 코드블록 안
- `[[링크]]` 슬러그. 슬러그를 영어로 바꿀 거면 전부 한 번에, 그리고
  `graph.json` 재생성까지가 한 벌이다. 지금 슬러그는 이미 영어다 — 안 바꾼다
- 페이지가 인용하는 실제 한국어 문자열 — 예: `ko.toml` 의 표지 낱말,
  `declared_continuation.py` 가 보는 어미 `-겠습니다`. 이건 데이터지 산문이 아니다

영어로 다시 쓰면서 `craft/emphasis-is-scarce` 도 같이 지킨다. 지금 훅이 막는
기존 파일이 15장이다 — `craft/` 8, `operator/` 3, 루트 4. 전부 이 단계의 재작성
대상이라 따로 정리하지 않고 여기서 한 번에 통과시킨다. 통과 여부는 `Write` 가
실제로 안 막히는 것으로 확인된다.

번역이 아니라 재작성이 맞는 자리가 있다. `craft/comments-carry-why.md` 의
"줄바꿈은 의미 단위에서 한다" 절은 한국어 관형형·의존명사 이야기다. 영어에는
그 문제가 없으므로 영어 기준(줄 끝에 남은 관사·전치사·접속사)으로 다시 쓴다.

### `tool/lint.py` — 줄바꿈 검사를 영어용으로 교체

| 지울 것 | 대신 |
| --- | --- |
| `ends_adnominal` | 없앤다 |
| `splits_a_phrase` | `orphan_tail(word)` — 줄 끝에 남은 관사·전치사·접속사·조동사 |
| `prose_lines` | 그대로 쓴다. 코드·표·목록·front matter 를 거르는 것은 언어와 무관 |
| `broken_wraps` | 이름 유지, 판정만 교체 |

- `test_lint.py` 의 해당 테스트를 영어 표본으로 바꾼다. 검사가 실제로 빨개지는지를
  보는 것이 요점이다 — 지금 테스트가 그렇게 쓰여 있다
- `lint.py:260` 의 한글 표시폭 주석(61%·54% 통계)은 근거가 사라졌으므로 지운다

### 상태·차단 UI의 한국어 유지

`statusMessage`와 훅의 사용자용 차단·오류 문구는 한국어로 유지한다. 이 단계에서는
영어 매핑이나 status 전용 갱신 로직을 추가하지 않는다. status 담당자는 두 호스트의
정상 상태와 한국어 description 차단 화면을 확인한다. 영어만 보이면 해당 문구를
한국어로 복구하고 재검증하며 enforce/gate 완료를 보류한다.

## 단계

| # | 이름 | 무엇 | 상태 |
| --- | --- | --- | --- |
| 1 | mirror | 양 호스트 출력 경로 조사·`mirror.py`·`test_mirror.py`·수동 실행 문서 | 완료 |
| 2 | mirror-live | Claude·Codex 각각 실제 세션과 한국어 표시 범위 검증 | 완료 — 양 호스트 실측. Codex 는 `CODEX_HOME` 을 찾고서야 보였다 |
| 3 | web-docs | 번역 API·인용 서랍·지도 한국어 표시와 graph의 Rule 파서 검증 | 완료 |
| 4 | enforce | 영어 진행·영어 Stop 판정·페이지 재작성·양 호스트 배선 전환 | 완료 |
| 5 | pages | Git 기준 manifest 확정·32개 산문 영어화·허용 보호 구간 차이 기록 | 완료 — manifest 37건. 규칙 24장 + 루트·문서 10장 영어화, 한국어로 남긴 3장은 `kept` 로 선언 |
| 6 | lint-index | 영어 줄바꿈 검사·직접 실행 검사·sync와 graph 각각 재생성 | 완료 |
| 7 | status | 두 호스트의 상태·차단·질문 UI 한국어 유지 확인 | 완료 |
| 8 | gate | `--check` 초록 + 표본 역번역 사람 확인 | 완료 — `translate --check` 37/37 종료 코드 0, 역번역 3건 사람이 읽고 "크게 무너지는 것 없음" |

## 검증

- `python tool/translate.py --check --manifest docs/translation-baseline.json operator/ craft/ docs/ *.md` — 종료 코드 0. 계획서는 제외하고 재작성 예외는 manifest의 허용 차이와 대조한다
- `raw/translate-review.md` 의 표본 역번역을 사람이 읽는다. 단계마다 페이지 3개
- `python tool/lint.py --check` · `python tool/apply.py --project . --agent claude --check` ·
  `python tool/apply.py --project . --agent codex --check` · `pytest tool/`
- `docs/development.md`의 직접 실행 검사 중 변경된 경로를 확인한다:
  `python tool/test_apply.py`, `python tool/test_lint.py`, `python tool/test_inject.py`,
  `python tool/test_declared_continuation.py`. 특히 apply·lint·declared_continuation의
  main 검사는 pytest 수집만으로 실행되지 않는다.
- `npm --prefix web run lint` · `npm --prefix web run build` 및 문서·지도 한국어 표시 실측
- `python tool/trigger_audit.py "raw/census-*.jsonl" --project .` — 실제 존재하는 동일
  census 입력을 전후에 재생해 페이지별 적중 목록을 비교한다. gate 담당자가 pages 전에
  같은 입력을 확보하고 baseline을 기록한다. 입력이 없으면 합성 회귀는 별도로 실행하되
  실제 발화 적중 검증은 보류로 남기며, 실측을 대신했다고 표시하지 않는다.
  이 도구는 자동 적중률 회귀 게이트가 아니다. progress 페이지 이름 변경은 대응시켜 비교하고,
  번역에 따른 비용 변화와 트리거 변화는 구별한다
- 실측: 한국어 발화 하나로 (a) 페이지가 영어로 주입되는가 (b) 미러 셀에
  한국어가 뜨는가 (c) 내가 한국어 `description` 을 쓰면 차단되는가

## 되돌리는 법

페이지·lint·훅·Codex wrapper·생성기를 같은 전환 단위로 되돌리고, 소유한 영어 훅
엔트리를 제거한 뒤 두 호스트 설정과 graph/corpus를 재생성한다. 기존 한국어 강제와
Stop 검사가 돌아오는지 확인한 후 미러를 닫는다. settings 한 곳만 빼는 것으로는 부족하다.

## 리뷰 반영 — 전환 전에 닫아야 하는 빈자리

- 현재 미러 명세는 Claude만 지원한다. Codex의 출력 수집 경로·레코드 형식·세션 식별을
  실제 호스트에서 확인하고 테스트 표본을 확보한 뒤 지원한다. 그 전에는 공통 페이지와
  Codex 강제를 영어로 전환하지 않는다. 질문 선택지와 훅 상태·오류는 한국어 예외로 남긴다.
- `declared_continuation.py`는 사용자가 아니라 assistant 응답의 한국어 어미를 검사한다.
  영어 진행 전환과 함께 영어 약속·질문 약속·유예·실제 질문의 판정을 추가하고 한·영
  회귀 표본을 검사한다. 원래 한국어 패턴은 실패 원문 경로를 위해 유지한다.
- `ask-with-arrow-key-options`의 한국어 표시 규칙과 `english-progress`의 예외를 맞춘다.
  웹 대화 답변은 3단계 전까지 한국어 출력을 유지한다. 웹 문서·지도 한국어 표시는
  이 단계 web-docs 담당자가 pages 전에 끝낸다. 실패하면 pages 배포와 gate를 보류한다.
