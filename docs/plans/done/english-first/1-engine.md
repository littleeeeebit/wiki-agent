# 1단계 — 번역 엔진과 입력 경로

전체 설계와 세 단계의 관계는 [개요](0-overview.md)에 있다.

목표. 사람이 한국어로 쓰면 에이전트가 영어로 받는다. 사용자 화면은 아직
아무것도 안 바뀐다 — 화면을 바꾸는 것은 2단계의 미러가 생긴 뒤다.

## 만들 것

### `tool/translate.py`

Gemini REST 를 `urllib` 로 직접 친다. 새 의존성 0개다 — `requirements-chat.txt`
는 `fastapi`·`uvicorn`·`PyYAML` 뿐이고 여기 더할 이유가 없다.

```
ko_to_en(text: str) -> str
en_to_ko(text: str) -> str
```

- 모델 `gemini-3.1-flash-lite`. 키는 `GEMINI_API_KEY` 환경변수 (이미 있다)

  실측이 계획을 고친 자리다. 처음 적은 `gemini-2.5-flash` 는 이 계정에
  404 다 — "no longer available to new users". `gemini-2.5-flash-lite` 도
  같은 404 라 2.5 세대는 통째로 못 쓴다. 후보를 재 보니 짧은 문자열
  2건 왕복이 `3.6-flash` 6.3초, `3.8-flash` 4.5초, `3.5-flash-lite` 1.2초였다.
  lite 둘만 세 회차씩 다시 재니 중앙값이 `3.1-flash-lite` 1.17초,
  `3.5-flash-lite` 1.00초다. 0.17초 차이로는 두 세대의 가격차를 못 산다 —
  사용자 판단으로 `3.1-flash-lite` 에 고정했다.
  큰 모델은 그 시간을 thinking 에 쓰는데, 보호 구간을 이미 빼낸 번역에서는
  그것이 사는 게 없다. lite 는 `thinkingConfig` 를 400 으로 거부한다 — 끌
  필요 없이 애초에 꺼져 있다. 발화마다 도는 경로이므로 싼 티어가 맞다.

  별칭은 안 쓴다. `gemini-flash-latest` 는 캐시 키를 안 바꾸고 모델만
  바꾸므로, 캐시가 지금 쓰지 않는 모델의 번역을 계속 내주게 된다.
- 실패하면 원문을 그대로 돌려준다. 예외도 타임아웃도 키 없음도 전부 그렇다.
  번역이 세션을 못 멈추게 한다 — `craft/hooks-fail-open`
- `stdin`·`stdout` UTF-8 고정, `subprocess` 를 쓸 일이 있으면
  `encoding="utf-8", errors="replace"`. `lint.fragile_tools`·`fragile_io` 가 검사한다

### 자리표시자 보호 — 번역기 판단에 안 맡긴다
번역 전에 아래를 토큰(``+index 같은 사용자 영역 문자)으로 빼내고, 번역 뒤 되돌린다.

| 보호 대상 | 왜 |
| --- | --- |
| 인라인 백틱 `` `...` `` | 명령·경로·식별자. 번역되면 실행이 깨진다 |
| 펜스 코드블록 ` ``` ` | 같은 이유, 통째로 |
| `[[링크]]` | 위키 링크. 슬러그가 바뀌면 `graph.json` 이 끊긴다 |
| YAML front matter | `triggers` 정규식이 여기 있다. 번역하면 주입이 죽는다 |
| `<!-- wiki:... -->` 주석 | `inject.py` 가 심는 출처 표지 |

### 용어집 `tool/markers/glossary.toml` (새 파일)

```toml
# keep_korean — 영어로 옮기면 다른 것을 가리키게 되는 말. 원문 그대로 남긴다.
keep_korean = ["전자조달", "나라장터", "입찰공고", "지방계약법", "낙찰하한율"]

# fixed — 이 위키 안에서 뜻이 정해진 말. 호출마다 다르게 번역되면 안 된다.
[fixed]
"위키" = "wiki"
"지뢰" = "landmine"
"계약" = "contract"      # 페이지 심각도. 법률상 계약이 아니다
"게이트" = "gate"
"주입" = "injection"
"발화" = "utterance"
"훅" = "hook"
```

두 표를 번역 프롬프트에 싣는다. `keep_korean` 은 "이 낱말은 한국어 그대로 두라",
`fixed` 는 "이 낱말은 반드시 이 영어로 옮기라".

### 캐시 `raw/translate-cache.sqlite3`

방향·원문·모델·프롬프트·용어집 버전 → 번역.
stdlib SQLite로 훅·미러의 동시 쓰기를 처리한다. 실패 결과는 캐시하지 않는다.
`raw/*`는 현재 `.gitignore` 대상이다.

### `translate.py --check`

```
python tool/translate.py --check --manifest docs/translation-baseline.json <경로...>
```

Git 원문과 번역 산출물의 기계 검사. `--source-root`는 커밋 전 작업 검사용 대체 입력이다.

| 검사 | 빨개지는 조건 |
| --- | --- |
| `keep_korean` 손실 | 원문에 있던 보존 용어가 번역본에 없다 |
| 백틱 변조 | 인라인·펜스 코드의 내용이 원문과 한 글자라도 다르다 |
| 깨진 `[[링크]]` | 링크 슬러그가 바뀌었거나 대상 페이지가 없다 |
| front matter 변조 | `triggers`·`scope`·`severity`·`links` 가 달라졌다 |
| 표본 역번역 | 표본 N개를 en→ko 로 되돌려 `raw/translate-review.md` 에 원문과 나란히 적는다. 이 항목은 종료 코드에 안 넣는다 — 사람이 읽는 자리다 |

### `tool/test_translate.py`

- 자리표시자 왕복 — 백틱·코드블록·`[[링크]]`·front matter 가 있는 문서를 넣어
  네트워크를 안 타고(번역 함수를 가짜로 바꿔 대문자화 같은 것만 하게) 보호 구간이
  한 글자도 안 바뀌는지 본다
- 키가 없을 때 `ko_to_en` 이 원문을 그대로 돌려주는지
- 자식 프로세스로 불렀을 때 한글이 파이프에서 안 죽는지 — `test_edit_as_diff.py` 의
  같은 테스트가 본이다

## 고칠 것

### `tool/inject.py` — 입력 ko→en

순서가 전부다. 아래 셋을 이 순서로 한다.

1. 발화를 한국어 원문 그대로 받아 `triggers` 정규식에 매칭한다 — 지금 하던 대로
2. 페이지를 고른다 — 지금 하던 대로
3. 그 다음에 `ko_to_en(발화)` 를 불러 주입문 끝에 영어본을 덧붙인다

2번과 3번을 바꾸면 트리거가 영어 문장에 한국어 정규식을 대는 꼴이 되어 주입이
전부 죽는다. 죽는데 조용히 죽는다 — `craft/hooks-fail-open` 의 그 모양이다.

- 발화에 한글이 없으면 번역을 건너뛴다. API 호출 하나를 아끼고, 영어 발화를
  다시 영어로 옮기는 헛일을 막는다
- `.claude/settings.json` 의 이 훅 `timeout` 을 10 → 15 로 올린다. Gemini 왕복이
  들어온다. 단 훅은 예산을 넘겨도 통과시켜야 하므로, `translate.py` 자체에
  더 짧은 자체 타임아웃(예: 6초)을 두고 넘으면 원문만 쓴다.
  설정 원본은 `apply.py::hook_entry/session_entry`다. 그곳을 고친 뒤
  `python tool/apply.py --project . --agent claude --write`와
  `python tool/apply.py --project . --agent codex --write`로 생성한다
- 주입 형식:

```
<!-- wiki:english-rendering -->
English rendering of the user's message (Gemini; the Korean above is authoritative):
...
```

한국어 원문이 정본이라고 적는 것이 중요하다. 번역이 틀렸을 때 에이전트가
원문으로 돌아갈 수 있어야 한다.

### `tool/session_state.py` — 세션 시작 컨텍스트 ko→en

사용자는 커밋 메시지·PR·`.wiki/decisions/` 를 계속 한국어로 쓴다. 그것이
에이전트 컨텍스트로 들어가는 `report()` 조립 시점에서 영어로 바꾼다.
발화별 결정 요약은 `inject`의 컨텍스트 조립 시점에서도 번역한다.

- `decisions()` 가 내는 `(title, why)` 두 문자열 — 커밋 제목과 `왜.` 첫 문장
- `active_page()` 가 내는 `.wiki/plan-active.md` 본문
- `open_steps()` 가 내는 계획 표의 행
- `branch_line()`의 기본 한국어 출력은 보존하고, `report()`만 영어 출력을 명시적으로
  선택하게 한다. 고정 문자열의 영어 표기는 직접 제공하고 Gemini에 맡기지 않는다
- `report()` 의 고정 산문(`## 브랜치`, `## 최근 결정 — 다시 뒤집기 전에 이유를 보라` 등)도
  마찬가지로 영어로 직접 고쳐 쓴다
- `open_steps()` 의 `## 단계` 표 파서와 `완료`·`취소`·`상태` 판정은 한국어 그대로
  둔다. 이 폴더의 계획서가 한국어 표를 쓰고, 사용자도 한국어로 쓴다.
  2단계에서 페이지가 영어로 가도 계획 문서는 사람이 쓰는 것이라 안 따라간다
- 이 훅 `timeout` 은 15 → 25. 결정 4건 + 계획 표를 번역한다

`decisions()`·`active_page()`·`open_steps()`는 한국어 정본을 읽는 함수로 유지한다.
`slack_brief.standup()`과 `chat.handoff()`도 이 함수들을 호출하므로 여기서 번역하면
1단계부터 Slack·웹 사용자 화면이 바뀐다. 번역은 `report()`의 출력 조립에만 적용하고
Slack·인계의 한국어 보존을 기존 검사에 추가한다.

번역은 문자열마다 6초씩 순차 호출하지 않는다. 훅 전체의 남은 시간 안에서 한 번에
묶어 번역하거나 공통 마감 시간을 전달하고, 시간이 다 되면 아직 번역하지 않은 부분은
원문으로 조립해 반드시 주입한다. 결정 네 건만 각각 기다려도 24초이므로 25초 설정만으로
실패 시 주입 보존을 보장하지 못한다. 네트워크 지연을 가짜로 주는 검사로 확인한다.

### 에이전트 전용 프롬프트 영어화 — 사용자 화면이 아니다

| 파일 | 비고 |
| --- | --- |
| `tool/prompts/chat-answer.md` | |
| `tool/prompts/chat-explain.md` | |
| `tool/prompts/slack-retro.md` | Slack 에 출력되는 문구는 한국어로 남긴다 |
| `tool/prompts/slack-standup.md` | 같음 |
| `skills/after-merge/SKILL.md` | `description` 의 한국어 트리거 낱말(`머지했다` 등)은 남긴다 |
| `skills/review-loop/SKILL.md` | 같음 |
| `skills/retrospect/SKILL.md` | 같음 |
| `skills/design-pass/SKILL.md` | 같음 |

스킬 `description` 의 한국어 트리거는 `triggers` 정규식과 같은 성질이다 —
사용자 발화에 걸리라고 있는 것이라 영어로 바꾸면 스킬이 안 뜬다.

`chat-answer.md`와 `chat-explain.md`는 이미 영어 지시문이다. 한국어 출력을 요구하는
지시는 3단계 웹 오버레이 검증까지 유지한다. 프롬프트의 언어와 출력 언어는 별개다.
`tool/chat.py::WIKI_WRITER`와 `CLAUDE_MD_WRITER`도 에이전트용 지시문이므로 이 단계에
영어로 옮기되 웹에 돌아오는 결과 설명은 한국어로 유지한다. UI 라벨·오류와는 구분한다.

## 안 건드리는 것

`tool/markers/ko.toml` · `census.py` 의 한글 낱말 추출 · 페이지 front matter 의
`triggers` · `korean_progress.py` (2단계) · `settings.json`의 `statusMessage` (한국어 유지) ·
페이지 산문 (2단계) · `tool/*.py` 주석 (3단계) · `lint.broken_wraps` (2단계)

## 단계

| # | 이름 | 무엇 | 상태 |
| --- | --- | --- | --- |
| 1 | translate | `tool/translate.py` + `glossary.toml` + 캐시 | 완료 |
| 2 | check | Git 기준 manifest·`translate.py --check`·원문/이름 변경/새 문서 표본·역번역 기록 | 완료 |
| 3 | test | `tool/test_translate.py` — 자리표시자 왕복·키 없음·파이프 인코딩 | 완료 |
| 4 | inject | `inject.py` 에 ko→en (트리거 매칭 뒤에) + timeout 15 | 완료 |
| 5 | session | `report()`에서만 결정·계획 번역, 공용 함수 한국어 유지 + timeout 25 | 완료 |
| 6 | prompts | 프롬프트 4개·스킬 4개·chat 인라인 writer 2개 점검/영어화, 출력 한국어 유지 | 완료 |
| 7 | gate | `tool/lint.py --check` 와 `pytest tool/` 초록 | 완료 |

## 검증

- `pytest tool/` 전부 초록
- `python tool/test_apply.py` · `python tool/test_inject.py` · `python tool/test_slack_brief.py`
  — 직접 실행 main 검사도 통과한다. pytest 수집 수로 이 검사 실행을 대신하지 않는다
- `python tool/lint.py --check` 초록 — 특히 `fragile_tools`·`fragile_io`·
  `missing_hook_guards` 가 새 `translate.py` 를 통과하는지
- `python tool/apply.py --project . --agent claude --check`와
  `python tool/apply.py --project . --agent codex --check` 초록 — 생성 원본과 배선을 대조한다
- 실측: 한국어 발화 하나를 실제로 쳐서 (a) 페이지가 여전히 주입되는가
  (b) 영어본이 붙는가 (c) 체감 지연이 얼마인가
- 키를 일부러 빼고 같은 발화 — 주입이 그대로 돌고 영어본만 없어야 한다

## 되돌리는 법

번역 호출·고정 산문·프롬프트·`apply.py`의 timeout 변경을 함께 되돌리고 두 호스트
설정을 재생성한다. 생성된 settings만 되돌리면 다음 apply가 다시 바꾼다.
캐시는 비활성화하거나 버전을 바꿔 옛 번역을 재사용하지 않게 한다.

## 리뷰 반영 — 번역 계약과 빠진 주입 경로

- `--check`는 원문 없이는 비교할 수 없다. 원문의 정본은 git 이다. 1단계 check
  담당자가 추적되는 `docs/translation-baseline.json` 형식과 검사기를 만든다. 각 대상에
  번역 전 고정 전체 commit SHA·원문 경로·산출물 경로·종류(번역/재작성/신규)를 기록한다.
  `HEAD`를 기본 원문으로 쓰거나 rename을 추측하지 않는다. 2단계 pages 담당자가 번역 전에
  실제 대상을 채운다. 새 클론은 지정 커밋을 포함한 이력이 필요하며 없으면 명시적으로 실패한다.
  `git show <SHA>:<원문 경로>`로 읽고, 페이지 이름 변경은 옛 경로를 명시한다.
  커밋 안 된 한국어 원문은 먼저 정본 커밋에 포함한 뒤 SHA를 고정한다. 그 전 작업 검사만
  `--source-root raw/translate-source`를 허용하며 배포 게이트 통과로 세지 않는다.
  처음부터 영어로 쓴 신규 문서는 신규로 명시하여 원문 비교 대상에서 제외하되 링크·형식
  검사는 받는다. 원문이 없다는 이유로 기존 번역 문서를 신규로 자동 분류하지 않는다.
  재작성·이름 변경의 보호 구간 예외는 파일별 이전 값과 허용 새 값을 manifest에 기록하여
  검토한다. 파일 전체의 검사를 끄지 않는다. 일반 번역 항목은 모든 보호 구간이 같아야 한다.
  원문 부재·빈 대상·보호 구간 변조는 실패다. 디렉터리와 `*.md` 패턴은 CLI 내부에서
  확장하되 `docs/plans/`는 번역 대상에서 제외한다. 재작성 페이지는 승인한 변경만
  별도로 기록하고 그 밖의 front matter 전체·링크·백틱은 원문과 대조한다.
  1단계에서는 임시 Git 저장소의 커밋·rename·신규·누락 원문 표본으로 검사기를 완료한다.
  실제 32개 영어 번역과 사용자 의미 검수는 2단계 gate 담당이 수행하므로 1단계 완료가
  2단계 산출물을 기다리지 않는다. manifest 자신과 계획서는 번역 대상에서 제외한다.
- 자리표시자의 유실·중복·변조를 복원 전에 검사한다. `keep_korean`도 프롬프트만
  믿지 말고 보호하며, 검증 실패 시 원문을 반환하고 실패 번역은 캐시하지 않는다.
  Markdown 링크의 목적지와 `{slot}`도 보호한다. 보호 토큰을 훼손하는 가짜 응답을 검사한다.
- 캐시 키에는 방향·원문 외에 모델·프롬프트·용어집 버전을 포함한다. 훅과 미러가
  동시에 쓰므로 stdlib SQLite 등 원자적 저장을 사용한다. 실패 원문은 성공 캐시에 넣지 않는다.
- `inject.py`의 `source_map`·고정 주입 산문·`knowledge/digest` 결정 요약과 대상
  `.wiki/*.md` 본문도 에이전트 입력이다. 한국어 정본과 트리거 판정은 보존하고,
  선택·요약 뒤에 번역한다. 페이지가 하나도 매칭되지 않아도 발화 번역은 출력한다.
  `trajectory`는 원문 발화를 계속 기록한다. 실패 시 영어본 표지를 붙이지 않는다.
- `session_state.doc_catalog()`의 문서 제목도 번역 대상이다. 카탈로그 전체를 코드
  펜스로 감싼 뒤 번역하면 보호에 걸리므로 제목만 먼저 번역하고 경로는 보존한다.
- `inject.shrink()`는 `규칙.`으로 문단을 찾는다. 2단계 본문 전환 전에 `Rule.`도
  인식하게 하고, 한·영 페이지의 작은 rule_budget에서 규칙 한 줄이 남는지 검사한다.
