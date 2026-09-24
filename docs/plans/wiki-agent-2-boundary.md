# 2단계 — 경계 검사

전체 설계와 단계의 관계는 [개요](wiki-agent-0-overview.md)에 있다.

목표. 파이프라인 넷이 각자 폴더에 들어가고, 폴더 사이의 import 가 하나라도 생기면
`lint --check` 가 빨개진다. 동작은 하나도 바뀌지 않는다 — 옮기고, 가르고, 검사를 세운다.

## 옮기는 것

| 지금 | 옮긴 뒤 | 부르는 쪽이 쓰는 이름 |
| --- | --- | --- |
| `tool/translate.py` | `tool/translate/__init__.py` | `import translate` 그대로 |
| `tool/sessions.py` | `tool/workspace/sessions.py` | `from workspace import sessions` |
| `tool/chat_session.py` | `tool/agent/chat_session.py` | `from agent import chat_session` |
| `tool/chat_local.py` | `tool/agent/chat_local.py` | `from agent import chat_local` |
| `tool/wikilib.py` | `tool/wiki/wikilib.py` | `from wiki import wikilib` |
| `tool/inject.py` 의 매칭·렌더링 | `tool/wiki/match.py` | `from wiki import match` |

`translate` 만 `__init__.py` 에 통째로 넣는다. 모듈 하나짜리 파이프라인이고 이름이 같아서,
부르는 쪽을 하나도 안 고친다. 나머지는 모듈 이름을 그대로 두고 폴더 안으로
넣는다. 공개 진입점을 하나로 모으는 일은 3~5단계가 한다.

`python tool/translate.py --check` 는 `python tool/translate --check` 가 된다.
`tool/translate/__main__.py` 가 그 입구다.

### `inject.py` 를 가르는 선

`inject.py` 에서 `translate` 를 부르는 곳은 셋이다 — `rendering`, `localised`, 그리고 그
둘이 나눠 쓰는 마감 `BUDGET`. 이것과 `main` 은 훅 진입점에 남고, 나머지는 전부
`wiki/match.py` 로 간다.

| `wiki/match.py` | `tool/inject.py` (훅 진입점) |
| --- | --- |
| `pages`, `match_pages`, `render_parts`, `fit`, `shrink`, `knowledge`, `digest`, `rule_index`, `source_map`, `label`, `slots_for`, `budget`, `adapter_path`, `fill`, `project_wiki` | `main`, `rendering`, `localised`, `BUDGET`, `MAX_RENDERED`, `HANGUL` |

`inject.py` 는 파일 이름과 자리를 지킨다. 사용자 단위 훅이 `hook.py claude inject.py` 로,
예전 프로젝트 단위 설치가 `tool/inject.py` 경로로 부르기 때문이다. `inject.py` 의 이름을
빌려 매칭 함수를 쓰던 곳(`apply`, `graph`, `trigger_audit`, `chat`, 테스트)은 `wiki.match`
를 직접 부르게 고친다. `inject.py` 가 다시 내보내는 길은 두지 않는다 — 그 길이 있으면
경계 너머를 부르는지 눈으로 알 수 없다.

## 옮기지 않는 것

| 무엇 | 왜 |
| --- | --- |
| `hook.py` 와 훅 스크립트(`inject.py`, `session_state.py`, `sync.py` 등) | 설치된 훅이 경로와 이름으로 부른다 |
| `graph.py` | 위키 지도를 만드는 명령행 도구다. 결과(`graph.json`)만 화면이 읽는다. `apply.runs` 를 쓰므로 `wiki/` 에 넣으면 경계를 넘는다 |
| `chat.py`, `chat_channels.py`, `chat_post.py`, `mirror.py`, `transcript.py` | 메인 쪽이다. 6단계에서 새로 짜거나 지운다 |
| 나머지 `tool/*.py` | 훅, 설치, 검사 도구. 파이프라인이 아니다 |

## Slack 삭제

지운다: `slack_brief.py`, `slack_post.cmd`, `prompts/slack-standup.md`,
`prompts/slack-retro.md`, `test_slack_brief.py`, `test_distribution.py` 의 Slack 실행기 테스트.
`chat.py` 가 쓰던 `repo_url` 은 `chat.py` 안으로 옮긴다. 문서의 Slack 절(`docs/publishing.md`,
`docs/development.md`, `docs/verification.md`)도 걷어낸다.

## 검사

`lint.pipeline_imports` 가 `tool/wiki/`, `tool/translate/`, `tool/agent/`, `tool/workspace/`,
`tool/common/` 아래의 모든 `.py` 를 `ast` 로 읽는다.

- 함수 안의 지연 import 도 센다. 지금 코드에 지연 import 가 많다
- 상대 import(`from .chat_local import ...`)는 같은 패키지 안이므로 통과. 파일이 든 폴더
  깊이만큼 올라가면 `tool/` 루트에 닿으므로 절대 import 와 같이 본다(`from .. import translate`)
- 같은 모듈의 `tool.` 철자(`from tool import translate`, `import tool.apply`)도 같이 본다.
  저장소 루트가 경로에 있으면 — pytest 가 그렇다 — 이 철자로도 import 된다
- 맨 앞 이름이 다른 파이프라인이거나 `tool/` 루트 모듈이면 발견 하나
- `common/` 은 어느 파이프라인도, 루트 모듈도 부르지 못한다

발견의 종류는 `파이프라인 경계` 다. `lint --check` 의 종료 코드를 빨갛게 만든다.

`tool/*.py` 만 보던 검사 넷(`fragile_tools`, `fragile_io`, `broken_wraps`, `korean_prose`)은
하위 폴더까지 본다. 안 그러면 옮긴 파일이 인코딩 검사에서 말없이 빠진다.

### 빨개지는지 보는 테스트

`test_lint.py` 에 버리는 위키를 만들고 심는다.

| 심는 것 | 기대 |
| --- | --- |
| `tool/wiki/a.py` 가 `import translate` | 빨강 |
| `tool/wiki/a.py` 가 함수 안에서 `from agent import chat_session` | 빨강 |
| `tool/wiki/a.py` 가 루트 모듈 `import apply` | 빨강 |
| `tool/wiki/a.py` 가 `from tool import translate`, `import tool.apply`, `from tool.translate import x` | 빨강 |
| `tool/wiki/a.py` 가 `from .. import translate` | 빨강 |
| `tool/common/c.py` 가 `import wiki` | 빨강 |
| `tool/wiki/a.py` 가 `from . import b`, `import re`, `from common import c` | 초록 |

그리고 이 저장소 자체가 초록이다 — `lint --check` 종료 코드 0.

## 검증

- `pytest tool/` — 바꾸기 전 281개 통과. 뒤에도 같은 수에서 Slack 테스트만큼만 준다
- `python tool/lint.py --check` 종료 코드 0
- 훅을 실제 경로로 한 번: `hook.py claude inject.py` 에 한국어 발화를 넣어 주입이 나오는지
- `python tool/translate --check` 가 전과 같은 결과
- `chat.cmd` 로 서버를 띄워 `#위키` 에 한 번 묻는다
