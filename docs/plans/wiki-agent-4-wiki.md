# 4단계 — `wiki` 독립

전체 설계와 단계의 관계는 [개요](wiki-agent-0-overview.md)에 있다.

목표. `wiki` 는 번역 없이 돈다 — 질문을 사람이 친 그대로 받아, 걸린 페이지를 쓰인 그대로
돌려준다. 루트 모듈은 `__all__` 로만 부른다. 3단계의 `translate` 와 같은 검사가 붙는다.

## 번역 없이 도는가

이미 그렇다. 2단계가 `inject.py` 를 갈라 번역을 훅 진입점에 남겼고, `pipeline_imports` 가
`wiki` 의 `translate` import 를 막는다. 이 단계는 그것을 실제 경로로 확인만 한다.

| 경로 | 번역을 끈 상태(`TRANSLATE_MONTHLY_USD=0`, 버리는 캐시) |
| --- | --- |
| 질의 — `inject.py` 훅 | 규칙 다섯 장이 주입됐다. 영어본만 빠진다 |
| 그래프 — `graph.py` | 처음부터 번역을 부르지 않는다. `graph.json` 이 나온다 |
| `import wiki` | `translate` 가 `sys.modules` 에 없다 |

## 공개 진입점

`tool/wiki/__init__.py` 의 `__all__` 이다. 부르는 쪽이 두 부류라 이름도 두 무리다.

| 무리 | 이름 | 부르는 쪽 |
| --- | --- | --- |
| 질의 — 발화가 무엇에 걸리나 | `pages`, `match_pages`, `render_parts`, `rule_index`, `source_map`, `label`, `budget`, `RULE_BUDGET`, `REPO_BUDGET` | `inject`, `chat`, `trigger_audit` |
| 페이지 형식 — 페이지를 어떻게 읽나 | `WIKI`, `SCOPES`, `front_matter`, `metadata_errors`, `links_of`, `resolve`, `hub_pages`, `project_pages`, `INJECTABLE`, `SLOT`, `adapter_path`, `slots_for` | `lint`, `graph`, `apply`, `repo_lint`, `repo_graph`, `session_state` |

`translate` 처럼 다섯 이름으로 줄이지 않았다. 루트의 도구들이 실제로 페이지 형식을 읽고,
그 이름을 감추려면 도구마다 새 함수를 만들어야 한다. 지금 쓰는 이름을 그대로 적고, 목록 밖의
것(`fit`, `shrink`, `knowledge`, `digest`, `fill`, `project_wiki`, `MAX_DECISIONS`)은 안에서
마음대로 바뀐다.

바꾼 것.

- `wikilib.pages` 를 `hub_pages` 로 바꿨다. `match.pages` 와 이름이 겹쳐 둘 다 공개할 수 없다.
  부르는 쪽은 `lint` 하나다
- `git_ok` 는 `lint.py` 로 옮겼다. 부르는 쪽이 `lint` 하나이고 위키가 아니라 git 이야기다
- 루트 모듈 아홉 개가 `from wiki.match import` 와 `from wiki.wikilib import` 대신
  `from wiki import` 를 쓴다. 테스트는 내부를 그대로 본다

## 답변 이벤트 계약

`wiki` 는 답변 문장을 만들지 않는다. 문장은 `agent` 가 CLI 로 만들고, 둘을 엮는 것은 메인이다.

| 누가 | 무엇을 |
| --- | --- |
| `wiki` | 질문 → 걸린 페이지 `(severity, body, path)` 목록. `label(path)` 이 이름 |
| `agent` | 답변 문장 — `delta`·`tool`·`done`·`error` 이벤트 |
| 메인 | 답변 스트림 앞에 `{"kind": "hits", "text": "", "pages": [이름, ...]}` 를 싣는다 |
| 화면 | 답의 `file:line` 을 인용으로 읽고 `/api/file` 로 연다(`Answer.tsx`) |

`hits` 모양은 지금 `chat.py` 와 `web/src/lib/api.ts` 의 `Ev` 가 이미 쓰는 것이다. 새 타입은
만들지 않았다. 6단계 새 메인이 같은 모양을 싣는다. 심각도나 본문이 화면에 필요해지면 그때
`pages` 옆에 붙인다.

## PR #4 에서 미룬 구멍

`import tool.translate` 는 `translate` 가 아니라 `tool` 이라는 이름을 묶는다. `pipeline_surface`
는 `translate` 만 보고 있어서 `tool.translate._ask()` 가 통과했다.

고친 것. `import tool` 이나 `import tool.<무엇>` 이 묶은 이름을 따로 모으고,
`<그 이름>.<파이프라인>.<속성>` 도 `__all__` 과 맞춰 본다. `import tool as t` 뒤의
`t.translate._ask()` 도 같이 잡힌다.

같은 PR 의 두 번째 P2(선차감이 출력 토큰의 상한이 아님)는 `translate` 의 요청 모양과 캐시를
바꾸는 일이라 이 단계에 넣지 않았다.

## 넣지 않은 것

| 무엇 | 왜 |
| --- | --- |
| `graph.py` 를 `wiki/` 로 | `apply.runs` 로 훅 설치 상태를 읽는다. 2단계 판단 그대로 루트의 도구로 둔다 |
| 질의를 한 함수로 묶기 | `inject` 는 매칭과 렌더링 사이에 번역을 끼워야 한다. 묶으면 다시 갈라야 한다 |

## 검증

| 확인 | 결과 |
| --- | --- |
| `pytest tool/` | 285 통과. 바꾸기 전과 같다 |
| `python tool/lint.py --check` | 종료 0. `chat.py` 에 `from wiki.match import fit`, `import tool.wiki` 뒤 `tool.wiki.fit`, `import wiki` 뒤 `wiki.shrink` 를 심으면 종료 1 과 `공개 진입점` 셋 |
| `python tool/test_lint.py` | 새 두 경우(`import tool.translate`, `import tool as t`)가 빨강. 고치기 전 `lint.py` 로 돌리면 둘 다 초록이라 검사가 이 수정을 본다. `import tool.translate` 뒤 공개 이름은 초록 |
| 직접 실행 스크립트 | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` 종료 0, `ruff check tool` 통과 |
| 훅 실제 경로 | 번역을 끈 채 `inject.py` 에 `git reset --hard 로 되돌려줘` — 규칙 다섯 장 주입. `session_state.py --project .` 종료 0 |
| 도구 | `graph.py`, `trigger_audit.py --help`, `chat.py --check` 통과 |
