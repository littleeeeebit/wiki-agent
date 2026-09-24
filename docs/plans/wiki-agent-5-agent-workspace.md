# 5단계 — `agent`·`workspace`

전체 설계와 단계의 관계는 [개요](wiki-agent-0-overview.md)에 있다.

목표. 에이전트가 쓰기를 할 수 있게 되고, 그 쓰기는 `workspace` 가 만든 작업트리 안에서
사람이 하나씩 승인한 것만 일어난다. 두 파이프라인이 `__all__` 을 갖고, `translate`·`wiki`
와 같은 검사가 붙는다. 화면은 없다 — 6단계가 이 계약 위에 짓는다.

## 승인은 CLI 가 이미 묻는다

두 CLI 모두 쓰기 전에 묻는 통로가 있다. 새로 짓는 것은 그 질문을 이벤트로 흘리고 답을
돌려보내는 것뿐이다. 2026-09-24 에 버리는 저장소에서 둘 다 실제로 돌려 확인했다.

| CLI | 띄우는 법 | 묻는 것 | 답하는 것 |
| --- | --- | --- | --- |
| Claude Code 2.1 | `claude -p` stream-json 에 `--permission-prompt-tool stdio` | stdout 의 `control_request` (`subtype: can_use_tool`) | stdin 의 `control_response` — `allow` 에 `updatedInput`, 또는 `deny` 에 `message` |
| Codex 0.156 | `codex app-server`, `thread/start` 에 `sandbox: read-only`, `approvalPolicy: untrusted` | 서버 요청 `item/commandExecution/requestApproval`, `item/fileChange/requestApproval` | 그 id 로 `{"decision": "accept"}` 또는 `"decline"` |

확인한 것. Claude 는 `Write` 를 허용하자 파일이 생겼고 `Bash` 를 거절하자 안 생겼다.
Codex 는 거절하자 둘 다 안 생겼고, 승인하자 둘 다 생겼다. Windows 의 Codex 는 파일도
PowerShell 명령으로 써서 `commandExecution` 승인으로 온다.

Codex 의 쓰기 세션만 `app-server` 로 띄운다. `codex exec` 은 승인을 받을 수 없다. 읽기 세션
(`chat.py` 의 채널, `explain`)은 지금의 `exec` 그대로 둔다. 둘을 하나로 합치는 것은 옛
`chat.py` 를 지우는 6단계가 할 일이다.

## `agent`

### 공개 진입점

| 이름 | 무엇 | 부르는 쪽 |
| --- | --- | --- |
| `ChatSession(repo, ..., write=False, parent_id=None)` | CLI 하나를 한 대화로. `say(text)` 가 이벤트를 흘린다 | `chat` |
| `ChatSession.answer(approval_id, allow)` | 승인 이벤트에 답한다 | 6단계 메인 |
| `Event` | 이벤트 하나 — 아래 계약 | `chat` |
| `explain` | 답을 쉬운 말로 다시 쓰는 격리 세션 | `chat` |
| `CodexServer`, `cli_command`, `settings`, `ROOT`, `SETTINGS` | CLI 찾기와 로컬 설정 | `chat_channels`, `setup_chat`, `setup_agents` |

### 이벤트 계약

```python
Event(kind, text="", meta={}, session_id="", parent_id=None)
```

| `kind` | `text` | `meta` |
| --- | --- | --- |
| `delta` | 답변 조각 | — |
| `tool` | 도구 한 줄 요약 | — |
| `approval` | 무엇을 하려는지 한 줄 | `id` (답할 때 쓴다), `tool`, `input` |
| `done` | 최종 답변 | `ms`, `error`, `session_id` (CLI 의 이어가기 id), `model`, `tokens`, `cost_usd` |
| `error` | 사람이 읽을 이유 | — |

`session_id` 는 이 프로그램의 세션 id 다. `ChatSession` 이 만들 때 정하고 끝날 때까지 바뀌지
않는다. 늦게 온 이벤트가 어느 세션 것인지는 이것이 판정한다. CLI 의 id 는 처음 응답이 와야
알 수 있고 모델을 바꾸면 다시 붙으므로 판정에 못 쓴다. 그것은 지금처럼 `done.meta` 에 남는다.
`parent_id` 는 부른 쪽이 넘긴다. 코디네이터가 워커를 띄울 때 쓸 자리이고 지금은 `None` 이다.

### 쓰기 세션

`write=True` 일 때만 쓰기 도구가 붙고 승인 통로가 열린다. 기본은 지금과 같은 읽기 세션이다.

- `workspace` 가 만든 작업트리가 아니면 만들지 않는다(`ValueError`). 작업트리의 부모 폴더가
  그 저장소의 `worktree_home` 이어야 한다. Orca 나 `claude -w` 가 만든 작업트리도 git 으로는
  같은 작업트리라, 연결된 작업트리인지만 보면 남의 작업에 쓴다(리뷰 라운드 1). 원본 체크아웃은
  자기 `-worktrees` 폴더 안에 있을 수 없으므로 같은 검사로 걸린다
- Claude. 도구는 `Bash,Read,Glob,Grep,Edit,Write`, 묻지 않는 도구(`--allowedTools`)는
  `Read,Glob,Grep` 뿐이다. `--permission-mode default` 를 명시해 설정의 `acceptEdits` 등이
  승인을 건너뛰지 못하게 한다
- Codex. `read-only` 샌드박스와 `untrusted` 승인. 알려진 읽기 명령 말고는 전부 묻는다
- 작업트리 밖으로의 쓰기는 사람에게 묻지 않고 거절한다. Claude 의 `Edit`·`Write`·`NotebookEdit`
  의 경로, Codex 의 `fileChange` 경로와 명령의 `cwd` 를 본다. 셸 명령 안의 경로는 읽지 않는다 —
  그것은 사람이 승인 이벤트에서 본다
- 승인을 기다리는 동안은 턴 마감(600초)이 흐르지 않는다. 사람이 자리를 비웠다고 턴이 죽으면
  안 된다
- 답은 물었던 프로세스로만 간다. 답이 늦는 사이 턴이 버려지고 다음 턴이 새 프로세스를 띄우면,
  지금 도는 프로세스로 보낸 "허용" 이 새 프로세스의 요청에 붙는다. Codex 는 프로세스마다 요청
  번호를 새로 센다(리뷰 라운드 1)

### 어느 계정으로 도는가

`craft/screen-ownership-before-wiring` 의 세 가지 가운데 둘은 위에 있다 — 늦은 이벤트는
`session_id` 가, 무엇을 써도 되나는 작업트리가 판정한다. 남은 하나가 계정이다.

세션은 이 프로그램을 띄운 환경의 로그인으로 돈다. Claude 는 그 사용자의 Claude Code 로그인,
Codex 는 `CODEX_HOME` 이 있으면 그것, 없으면 `~/.codex` 의 로그인이다. `ChatSession` 을 만들 때
환경을 통째로 붙잡아 두고 프로세스를 띄울 때마다 그것을 넘긴다. 그래서 재시작이나 `--resume`
도 같은 계정으로 이어진다. 처음에는 띄울 때의 환경을 그대로 물려받아서, 그 사이 서버의
`CODEX_HOME` 이 바뀌면 같은 세션이 다른 계정으로 이어질 수 있었다(리뷰 라운드 2). 계정을 고르는 화면은 이 계획에 없다.
Orca 가 계정마다 `CODEX_HOME` 을 따로 두던 방식은 가져오지 않는다

### 안 막는 것

사용자나 대상 저장소의 설정에 있는 `permissions.allow` 규칙은 CLI 가 먼저 적용하므로 그
규칙에 걸린 쓰기는 묻지 않는다. 사람이 직접 적은 허용이므로 그대로 둔다. `permissions.deny`
와 위키 훅도 같은 이유로 그대로 붙는다.

## `workspace`

### 공개 진입점

| 무리 | 이름 | 부르는 쪽 |
| --- | --- | --- |
| 작업트리 | `create`, `worktrees`, `remove` | 6단계 메인 |
| 세션 로그 | `SESSIONS`, `INJECTED`, `MAX_HUMAN_CHARS`, `parse`, `checkout`, `checkouts`, `folder`, `logs`, `FINDERS` | `census`, `transcript`, `hook`, `mirror`, `setup_agents` |

### 작업트리

| 함수 | 하는 일 |
| --- | --- |
| `create(repo, task)` | `../<repo>-worktrees/<task>` 에 브랜치 `<task>` 로 `git worktree add`. 경로를 돌려준다 |
| `worktrees(repo)` | 그 폴더 아래 작업트리. 행마다 `path`, `branch`, `dirty`, `merged` |
| `remove(repo, path)` | `git worktree remove`, 그리고 브랜치 삭제 |

- `repo` 는 원본 체크아웃이어야 한다. 작업트리 안에서 작업트리를 만들면 폴더가 엉뚱한 데 선다
- `task` 는 소문자·숫자·`-` 만, 64자까지. 브랜치 이름과 폴더 이름을 겸하고, 한글 경로가 이
  기계에서 `cp949` 로 깨진 적이 있다
- 폴더 자리(`worktree_home`)는 `tool/common/` 에 있다. `workspace` 가 거기 만들고 `agent` 가
  거기서만 쓰기 세션을 연다. 개요가 말한 "두 파이프라인이 실제로 같이 쓰는 것" 의 첫 예다
- `merged` 는 "지워도 원본 체크아웃의 HEAD 에 없는 것을 잃지 않는다" 는 뜻이다. 머지 기반부터
  브랜치까지 바뀐 모든 경로가, 지금 HEAD 에서 브랜치의 판과 똑같으면(같은 blob, 같은 모드) 참이다.
  경로는 `git diff --name-only --no-renames` 두 번으로 얻는다 — 기반과 브랜치 사이, 브랜치와 HEAD
  사이. 둘이 겹치지 않으면 참. squash 머지, 보통 머지, 자기 변경이 없는 브랜치가 참이다. 화면은
  이것을 보고 정리를 제안한다
- 시뮬레이션이 아니라 객체 비교다. 앞의 네 판정은 각각 유일한 커밋을 지웠는데, 넷 다 git 에게
  무언가를 상상하게 했고 그 상상에는 저마다의 규칙이 있었다

  | 판정 | 틀린 경우 | 라운드 |
  | --- | --- | --- |
  | 원격 브랜치가 지워졌다(`gone`) | 머지 없이 원격만 지울 수 있다 | 1 |
  | `git cherry` 로 같은 패치 | HEAD 가 나중에 revert 해도 이력에서 찾는다 | 2 |
  | diff 를 HEAD 에 거꾸로 적용(`git apply -R`) | 같은 줄이 파일의 다른 자리에 있으면 거기서 맞춘다 | 3 |
  | HEAD 에 합친 트리(`merge-tree`), 충돌 종료 코드는 버림 | 수정/삭제 충돌은 HEAD 의 판을 남겨 트리가 HEAD 와 같다 | 4 |
  | 같은 것, 종료 코드도 봄 | `.gitattributes` 의 `merge=ours` 같은 병합 드라이버가 브랜치 쪽을 깨끗이 버린다 | 5 |

- 라운드 3 에서 충돌 종료 코드 검사를 지웠었다. 지워도 테스트가 초록이라 군더더기로 봤다.
  초록은 그 검사를 보는 테스트가 없다는 뜻이었지 필요 없다는 뜻이 아니었다(라운드 4)
- 이름 바꾸기 감지는 끈다. 켜면 `a→b` 이름 바꾸기가 `b` 한 경로로만 보여, HEAD 가 `b` 를 따로
  가지면서 `a` 를 남긴 경우 `a` 를 지운 일이 사라진다
- 확실하지 않으면 거짓이다. 아직 pull 하지 않은 HEAD, 머지 뒤 HEAD 가 브랜치가 고친 파일을 다시
  고친 것은 브랜치를 남긴다. 병합 시뮬레이션보다 남기는 경우가 많다. 남은 브랜치는 목록에 한
  줄이지만, 잘못된 참은 일을 지운다
- `remove` 는 더러운 작업트리를 지우지 않는다. 브랜치는 `merged` 일 때만 `-D` 로 지우고
  나머지는 남긴다. 그 폴더 밖의 경로는 받지 않는다

## 검사

`lint.pipeline_surface` 는 `__all__` 이 있는 파이프라인을 본다. 두 `__init__.py` 에 `__all__`
을 쓰는 것만으로 붙는다. 루트 모듈은 `from agent.chat_session import` 대신 `from agent import`
를 쓴다.

## 넣지 않은 것

| 무엇 | 왜 |
| --- | --- |
| Codex 읽기 세션을 `app-server` 로 | 지금 도는 것을 바꿀 이유가 쓰기에는 없다. 6단계가 옛 `chat.py` 와 같이 정리한다 |
| Codex 쓰기 세션의 토큰 수 | `thread/tokenUsage/updated` 에 있다. 화면이 사용량을 그릴 때 붙인다 |
| 승인의 "이 세션 동안 허용" | 모든 쓰기를 승인받기로 했다. 필요해지면 `acceptForSession` 과 Claude 의 `permission_suggestions` 가 있다 |
| 코디네이터와 워커 | 별도 계획서. 이벤트에 `parent_id` 자리만 둔다 |

## 검증

| 확인 | 결과 |
| --- | --- |
| `pytest tool/` | 301 통과. 전 285, 새 테스트 16개(`test_agent` 6, `test_worktrees` 10) |
| `test_agent.py` 가 무엇을 보나 | 대기 중 턴 마감을 지우면, 작업트리 밖 거절을 지우면, 남의 작업트리 거절을 지우면, 답을 물었던 프로세스 대신 지금 프로세스로 보내면 각각 빨강. 승인은 화면처럼 다른 스레드에서 두 마감보다 늦게 보낸다 |
| `test_worktrees.py` 가 무엇을 보나 | 앞의 다섯 판정이 틀린 경우와 이름 바꾸기를 하나씩 심는다. HEAD 비교를 지우거나 겹침 검사를 지우면 여덟, 이름 바꾸기 감지를 켜면 하나, `remove` 가 `merged` 를 무시하면 둘이 빨강 |
| `python tool/lint.py --check` | 종료 0. `census.py` 에 `from workspace.sessions import codex_homes`, `workspace.home`, `agent.READ_TOOLS` 를 심으면 `공개 진입점` 셋 |
| `python tool/test_lint.py`, `ruff check tool` | 통과 |
| 직접 실행 스크립트 | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` 종료 0. `chat.py --check` 통과 — 읽기 세션은 그대로 돈다 |
| 실제 Claude (haiku) | `create` 로 만든 작업트리에서 두 파일을 쓰라고 했다. 승인 이벤트 둘, 하나 허용·하나 거절 → 허용한 파일만 생겼다. 두 번째 턴이 같은 프로세스에서 이어졌다. 작업트리 밖 경로에 쓰라고 하자 묻지 않고 거절했고 파일은 없다 |
| 실제 Codex (`app-server`) | 같은 지시. `fileChange` 승인이 경로와 함께 왔고, 허용한 파일만 생겼다. 두 번째 턴이 이어졌다 |
| 정리 | 더러운 작업트리 둘을 `remove` 가 거절했다. 치운 뒤 작업트리와 브랜치가 지워졌다 |
| 리뷰 라운드 1 | P0 하나(`gone` 으로 `-D` — 미병합 커밋 유실), P1 둘(남의 작업트리에서 쓰기 세션, 늦은 답이 새 프로세스로). 셋 다 재현한 뒤 고쳤다. 실제 Claude 로 쓰기·승인·정리를 다시 돌렸다 |
| 리뷰 라운드 2 | 계획 대비 점검 표 — 모든 항목이 됨 또는 다음 단계 몫, 둘만 일부. P0 하나(`git cherry` 가 revert 된 패치도 머지로 봄), P1 하나(계정이 세션 생성 때 고정된다는 서술이 코드와 다름). 둘 다 재현한 뒤 고쳤다 |
| 리뷰 라운드 3 | P0 하나(`git apply -R` 이 같은 블록의 다른 자리에서 맞춤). 같은 함수의 세 번째라 판정을 삼방향 병합의 트리 비교로 바꿨다 |
| 리뷰 라운드 4 | P0 하나(수정/삭제 충돌에서 합친 트리가 HEAD 와 같음). 라운드 3 에서 지운 충돌 종료 코드 검사를 되살렸다 |
| 리뷰 라운드 5 | P0 하나(`merge=ours` 드라이버가 브랜치 변경을 버린 병합이 HEAD 와 같음). 같은 함수의 다섯 번째라 병합 시뮬레이션을 버리고 경로별 객체 비교로 바꿨다 |
