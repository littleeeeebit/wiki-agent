# 3단계 — 코드 주석과 웹 UI

전체 설계와 세 단계의 관계는 [개요](0-overview.md)에 있다.

목표. `tool/*.py` 의 주석·docstring 이 영어가 되고, 웹 채팅에도 한국어 오버레이가 선다.

선행 조건. 기본 순서는 2단계 완료 뒤다. 2단계 미러가 보류되면 1단계 번역 엔진과
가짜 영어 응답으로 주석·대화 UI 개발을 먼저 진행할 수 있다. 문서·지도 표시와 번역 API는
2단계 담당이며 이 단계에서 중복 구현하지 않는다. 실제 영어 출력 활성화는 필요한
양 호스트 주입과 웹 오버레이 검증이 끝난 경로에서만 한다.

## 고칠 것 — 코드 주석

`tool/*.py` 8,699줄. `craft/comments-carry-why` 의 규칙이 그대로 적용된다 —
주석은 이유를 들고 이력은 안 든다. 번역하면서 그 규칙을 어기지 않는다.

### 번역이 아니라 재작성인 자리

이 주석들은 한국어라는 사실 자체가 내용의 일부다. 옮기면 뜻이 없어진다.

| 자리 | 지금 뭐라고 하나 | 어떻게 |
| --- | --- | --- |
| `hooks-fail-open` 계열 주석 — "메시지에 한글이 섞이면 그 stderr 쓰기가 또 죽는다" | 한글 때문에 cp949 stderr 가 죽는다 | 영어로 쓰되 이유를 유지한다: non-ASCII in the message kills the stderr write under a cp949 console |
| `korean_progress.py` 의 주석 전부 | 한국어 강제의 이유 | 2단계에서 규칙이 뒤집혔으므로 이미 재작성됐다 |
| `ko.toml` 의 주석 | 한국어 표지 목록의 유지 방법 | 한국어로 남긴다. 이 파일의 내용이 한국어고, 이것을 손보는 사람은 한국어 표지를 읽는다 |
| `census.py:277` 의 `[가-힣]{2,}` 옆 주석 | 한글 낱말 추출 | 영어로 쓰되 왜 한글 클래스가 필요한지를 남긴다 |
| `harvest.py:147` "한글 표지는 낱말 경계가 없으므로" | 한국어 표지 매칭 | 영어로 쓰되 합성어·조사가 붙은 표지는 정규식 낱말 경계만으로 잡히지 않아 포함 검사를 쓴다고 설명한다. 한국어에 낱말 경계 자체가 없다는 일반화는 피한다 |
| `test_harvest.py::test_한글_표지는_...` 같은 한국어 테스트 함수명 | | 아래 참조 |
| `test_codex_hooks.py` 의 `"한글 project"` 경로, `test_local_adapter.py` 의 `"adapter 한글 "` | 경로에 한글이 있을 때를 재현하는 데이터 | 그대로 둔다. 이건 테스트 입력이다 |

### 한국어 테스트 함수명

`test_harvest.py::test_한글_표지는_아직_합성어_안에서도_걸린다` 같은 이름이 있다.
영어로 바꾼다. 단, 그 테스트가 지키는 회귀 원인을 주석으로 옮겨 적는다 —
`comments-carry-why` 가 "테스트 주석은 반대로 판단한다"고 못 박은 자리다.
이름이 들고 있던 맥락이 이름과 함께 사라지면 안 된다.

### 순서

파일 수가 많아 한 번에 다 하면 리뷰가 불가능하다. 묶음으로 낸다.

| 묶음 | 파일 |
| --- | --- |
| 훅 | `inject.py` `session_state.py` `sync.py` `declared_continuation.py` `edit_as_diff.py` `english_progress.py` `codex_pretool.py` `hook_diagnostics.py` |
| 검사 | `lint.py` `repo_lint.py` `apply.py` `setup_agents.py` `setup_chat.py` |
| 분석 | `census.py` `harvest.py` `corpus.py` `graph.py` `repo_graph.py` `intersect.py` `transcript.py` `trajectory.py` `trigger_audit.py` `wikilib.py` `declared_continuation.py` |
| 채팅 | `chat.py` `chat_channels.py` `chat_local.py` `chat_post.py` `chat_session.py` `slack_brief.py` `translate.py` `mirror.py` |
| 테스트 | `test_*.py` 전부 |

## 고칠 것 — 웹 채팅 오버레이

### `tool/chat.py`

- 2단계의 `POST /api/translate`를 대화 표시에도 재사용한다. 새 번역 로직을 만들지 않는다
- 입력 번역 책임은 1단계 `UserPromptSubmit`의 `inject.py`가 진다. `chat.py`는 한국어
  원문을 호스트에 그대로 전달하고 선번역하지 않는다. 웹 담당자가 양 호스트의 실제
  채팅 세션에서도 훅 주입이 도는지 확인한다. 안 돌면 해당 호스트 입력 전환은 보류하고
  배선을 수리한다. 직접 이중 번역을 추가해 우회하지 않는다
- `raw/chat/progress.jsonl` 은 지금 한국어로 쌓여 있다. 앞으로는 영어가 쌓인다.
  기존 줄은 안 건드린다. 읽는 쪽이 한글 여부로 판단하게 한다

### `web/src`

- 툴바에 `한국어/English` 토글. 기본값은 한국어
- `Stream.tsx`·`Answer.tsx` 가 렌더 직전에 `/api/translate` 를 태운다. 응답을 캐시한다
- 토글이 꺼져 있으면 호출 자체를 안 한다
- 번역 실패는 원문을 보여준다. 빈 화면보다 영어가 낫다
- **사람이 친 발화는 오버레이에서 번역하지 않는다.** 언어로 판단하지 말고 발화자로
  판단한다 — 사용자는 한국어도 영어도 친다. 입력 번역은 1단계 `inject.py` 가 에이전트에게
  주는 것이고, 오버레이는 사용자가 "내 말이 어떻게 전달됐나"를 확인하러 오는 자리다.
  자기가 쓴 문장이 역번역돼 돌아오면 그 확인이 무의미해진다. 미러는 `SELF` 표지로 이미
  이렇게 하고(`tool/test_mirror.py` 의
  `test_the_persons_own_words_are_never_translated_in_either_language`),
  웹 대화·`Handoff.tsx` 도 같은 규칙을 따른다

웹 오버레이를 검증한 뒤 `chat-answer.md`의 한국어 답변 지시를 영어로 전환한다.
이미 영어인 프롬프트를 다시 번역하는 것만으로 출력 언어는 바뀌지 않는다.
`chat-explain.md`의 쉬운 한국어 설명은 번역과 다른 기능이므로 유지하고 중복 번역하지 않는다.
`chat_channels.py`의 review preamble에는 별도의 한국어 결과 지시가 있으므로 함께
영어로 바꾼다. retro의 `retro-candidates` 펜스는 한국어 부류와 고정 형식을 쓰는 데이터라
보존하고 UI 파싱 회귀를 검사한다. 2단계의 문서·지도 한국어 표시를 계속 유지한다.
렌더마다 부분 답 전체를 재번역하지 말고 완결된 구간을 번역하며, 이전 요청이 늦게
끝나도 새 답변·새 토글 상태를 덮지 않게 한다. 이 동작은 가짜 번역 응답으로 확인한다.

입력 원문은 `remember`·`hits_for`·호스트 훅의 트리거 매칭까지 보존한다.
`Handoff.tsx`가 보여 주는 최근 대화도 영어 로그를 담게 되므로 표시 오버레이에 포함한다.
복사할 원본 프롬프트와 한국어 미리보기는 구분하고, 번역문을 원본 로그에 덮어쓰지 않는다.

## 안 건드리는 것

`tool/markers/ko.toml` 의 표지와 주석 · 테스트의 한글 입력 데이터 ·
페이지 front matter 의 `triggers` · `raw/` 의 기존 로그 · 기존 커밋과 결정 기록

## 단계

| # | 이름 | 무엇 | 상태 |
| --- | --- | --- | --- |
| 1 | hooks | 훅 묶음 주석 영어화 | 완료 — 한글 든 줄 285 → 83 |
| 2 | checks | 검사 묶음 주석 영어화 | 완료 — 320 → 172 |
| 3 | analysis | 분석 묶음 주석 영어화 | 완료 — 361 → 180 |
| 4 | chat-tools | 채팅 묶음 주석 영어화 | 완료 — 315 → 178. `translate`·`mirror` 는 2단계에서 이미 영어 |
| 5 | tests | 테스트 주석·함수명 영어화, 회귀 원인 보존 | 완료 — 한글 함수명 65 → 0, 수집 수 244 유지 |
| 6 | input | 기존 번역 API 재사용·웹 양 호스트의 inject 입력 번역 실측 | 완료 — 실측이 결함을 냈다. 아래 참조 |
| 7 | web | 대화·인계 토글/오버레이·답변 및 채널별 출력 지시 전환 | 완료 — `useOverlay`, 토글, `chat-answer.md` 영어 전환 |
| 8 | gate | `pytest tool/` + `lint --check` + 웹 빌드 | 완료 — 245 통과, 직접 실행 7개, tsc·lint·빌드 |

남은 한글은 전부 의도한 것이다. 사람이 읽는 문자열(거절문 · `systemMessage` ·
argparse 도움말 · 화면 라벨 · Slack 본문), 파서가 대조하는 데이터(`## 단계` ·
`완료` · `왜.` · 한국어 약속 패턴), 테스트 입력, 그리고 영어 주석 안에 인용된
한국어 예시.

## 6걸음이 낸 것 — 주입 순서

실측하러 갔다가 결함을 찾았다. 웹 채팅에서 양 호스트로 한국어를 보내고 세션
로그를 열어 보니 규칙 묶음은 들어갔는데 **발화 영어본이 없었다.** 아무도 그렇게
말해 주지 않았다.

호스트는 주입이 약 12KB 를 넘으면 파일로 빼고 세션에는 2KB 미리보기만 준다.
평범한 턴의 규칙만으로 12,205자다. 영어본이 맨 뒤에 붙어 있었으므로 잘리는 쪽이
언제나 그것이었다. 영어본을 맨 앞으로 옮겼다 — 수백 자짜리이고, 사용자가 "내
말이 어떻게 전달됐나" 를 확인하러 오는 바로 그 블록이다.

| | 고치기 전 | 고친 뒤 |
| --- | --- | --- |
| Claude 웹 세션 | 규칙 O · 영어본 X | 둘 다 O |
| Codex 웹 세션 | 규칙 O · 영어본 X | 둘 다 O |

## 7걸음 실측 — `#위키` 채널

| 재는 것 | 결과 |
| --- | --- |
| 토글 기본값 | 한국어 |
| English 로 끄면 | `/api/translate` 요청 0건 |
| 완결된 답 하나 | 요청 1건. `!m.pending` 이 없을 때는 3건이었다 |
| 사용자가 영어로 친 질문 | 그대로. 언어가 아니라 발화자로 판단한다 |
| 영어 답 | 한국어로 떴다 |

## 검증

- `pytest tool/` 전부 초록 — 함수명을 바꿨으므로 수집 수가 전과 같은지 센다
- `docs/development.md`의 직접 실행 검사 7개도 실행한다: `test_lint.py`,
  `test_apply.py`, `test_inject.py`, `test_declared_continuation.py`, `test_repo_lint.py`,
  `test_slack_brief.py`, `test_trajectory.py`를 각각 `python tool/<파일>`로 실행한다.
  pytest 수집 수 보존만으로 main 기반 검사를 검증했다고 하지 않는다
- `python tool/lint.py --check` — 특히 교체된 `broken_wraps` 가 영어 주석에 대고
  오탐을 안 내는지
- `cd web && npm run build` 초록
- 실측: 양 호스트에서 한국어 입력·영어 주입을 확인하고, 정확한 답변의 한국어 표시와
  English 원문 토글을 확인한다. 쉬운 설명은 토글과 무관하게 한국어 기능임을 표시한다.
  review 채널, retro 후보 버튼, 인계 미리보기와 원문 복사도 검증한다
