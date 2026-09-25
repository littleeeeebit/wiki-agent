# 2단계 — 에이전트의 미룬 것

전체 설계와 단계의 관계는 [개요](0-overview.md)에 있다.

목표. 3·4단계가 얹을 긴 일을 받칠 수 있게 작업 세션을 고친다. 턴은 요청 하나보다 오래 살고,
화면은 새로 고친 뒤 도는 턴에 다시 붙는다. 무엇을 허용하고 거절했는지가 기록에 남는다. 같은
쓰기를 매번 묻지 않도록 "이 세션 동안 허용" 을 둔다. Codex 는 초점 세션도 쓰기 세션과 같은
`app-server` 로 돌고, 토큰 수를 낸다.

## 사용자와 정한 것

2026-09-25.

| 무엇 | 정한 것 |
| --- | --- |
| 창을 닫을 때 | 재접속은 새로 고침, 웹뷰 재적재, 브라우저 탭을 다시 여는 것만 잇는다. 앱 창을 닫으면 서버가 내려가고 턴도 멈춘다. 도는 턴이 있으면 닫기 전에 확인받는다. 트레이와 따로 뜨는 서버는 하지 않는다 |
| "이 세션 동안 허용" 의 폭 | 파일 쓰기(Claude `Edit`·`Write`·`MultiEdit`·`NotebookEdit`, Codex `fileChange`)는 도구 단위. 명령(Claude `Bash`, Codex `command`)은 글자까지 같은 명령과 같은 `cwd` 만 |
| Codex 읽기 세션 이전 | 초점 세션만 `app-server` 로 옮긴다. 쉬운 설명(`explain`)은 `exec` 에 남긴다. `app-server` 에는 `--ignore-user-config` 에 해당하는 것이 없어 사용자 `config.toml` 의 hook·MCP·프로필이 섞인다 |

개요의 "창을 닫았다 열어도 루프는 돌고 있고" 는 첫 결정에 맞춰 고쳤다. 루프는 새로 고침을
넘어 살고, 앱을 닫으면 멈춘다.

## 먼저 — 계획 문서의 순서

`session_state.plans` 는 `docs/plans/` 아래 문서를 경로의 역순으로 늘어놓고 앞의 둘(`MAX_PLANS`)만
읽는다(`tool/session_state.py:111`). 시리즈 폴더가 하나이던 때는 최신 시리즈가 앞에 오라는 뜻이었다. 이제
`loop/` 에 단계 문서가 여덟 있어 `7-verify.md` 와 `6-screen.md` 가 앞에 온다. SessionStart 는 다음에 할
2단계가 아니라 7·6단계의 행을 보인다. 3단계의 후보 재료도 같은 함수를 읽는다.

고침. 시리즈 폴더끼리는 지금처럼 최신이 앞이다. 한 폴더 안에서는 앞 번호가 먼저다. 그러면 `0-overview.md`
와 가장 앞의 남은 단계 문서가 읽힌다.

테스트. `a/0-overview.md`, `a/1-x.md`(다 끝남), `a/2-y.md`, `a/7-z.md` 를 두고 `plans` 가 `0-overview`
와 `2-y` 를 내는지 본다. 지금 코드에서 빨강이어야 한다.

## 턴 재접속

### 지금

턴은 `/api/work/say` 의 응답 본문이다(`tool/main/work.py:256`). 화면이 끊기면 본문 생성기가
닫히고, `ChatSession._say` 의 `finally` 가 끝나지 않은 턴의 프로세스를 닫는다
(`tool/agent/chat_session.py:442`). 연결이 끊기는 것이 곧 멈춤이다. 작업트리의 잡음(`_busy`)도
본문이 끝나거나 수거될 때 풀린다(`query.held`).

### 바꾸는 것

턴을 응답에서 떼어 스레드에서 돌린다. 이벤트는 작업트리마다 둔 버퍼에 쌓이고, 응답은 그 버퍼를
꼬리 문다.

| 자리 | 무엇 |
| --- | --- |
| `Run` | 작업트리 하나의 지금 턴. `turn`(턴 id, `uuid4().hex`), `session_id`, `events`(순서대로), `done`, 깨우기용 `threading.Condition` |
| `_runs: dict[str, Run]` | 작업트리 경로 → 마지막 턴. 다음 턴이 시작될 때까지 남는다. 끝난 직후에 다시 붙은 화면도 끝을 받는다 |
| `tail(run, after)` | `after` 다음 이벤트부터 흘리고, 새 것이 없으면 `Condition` 에서 기다린다. `done` 이고 다 흘렸으면 끝난다. 화면이 떠나도 턴에는 아무 일도 없다 |

이벤트 하나는 지금의 모양에 `seq`(버퍼 안 순번, 0부터)와 `turn` 을 더한다.

엔드포인트.

| 길 | 하는 일 |
| --- | --- |
| `POST /api/work/say` | 받는 순간 작업트리를 잡는다(지금과 같다). 턴 스레드를 띄우고 `tail(run, -1)` 을 돌려준다. 화면 코드는 지금처럼 한 요청으로 받는다 |
| `GET /api/work/events?path=&turn=&after=` | 다시 붙기. `turn` 이 지금 버퍼의 턴이 아니면 410. 경로는 `ours` 로 본다 |
| `GET /api/work/log` | 지금의 기록에 `running: {turn, session_id, seq}` 를 더한다. 도는 턴이 없으면 `null` |
| `POST /api/work/stop` | `{path, turn}`. 그 턴이 아직 돌면 멈춘다. 다른 턴이면 409 |

잡음은 턴 스레드가 쥐고 스레드의 `finally` 에서 놓는다. `work.say` 는 더 이상 `query.held` 를
쓰지 않는다. 스레드를 띄우지 못하면 그 자리에서 놓는다. 기록(`remember(path, "assistant", …)`)도
스레드의 `finally` 에서 한다. 화면이 없는 동안 끝난 턴도 기록에 남는다.

### 멈추기

연결이 끊겨도 턴이 돌기 때문에, 멈추는 길이 따로 있어야 한다. `ChatSession.stop()` 은 지금의
`close()` 와 같이 프로세스를 닫는다. `_drain` 이 `__closed__` 를 받아 턴이 `error` 로 끝나고,
이유는 "사람이 멈춤" 으로 바꿔 적는다. CLI 의 `session_id` 는 남으므로 다음 턴은 `--resume`·
`thread/resume` 으로 이어진다.

ponytail: Claude 의 `interrupt` 제어 요청과 Codex 의 `turn/interrupt` 는 쓰지 않는다. 프로세스를
닫는 것이 두 호스트에 같고 이어가기도 잃지 않는다. 다음 턴의 기동 시간(수 초)이 비용이다. 멈춤이
잦아지면(4단계의 루프) 그때 바꾼다.

### 화면

- 처음 적재와 새로 고침. `/api/work/log` 의 `running` 이 있으면 기록 뒤에 `pending` 인 답 턴을
  하나 붙이고 `events?after=-1` 로 붙는다. 버퍼가 처음부터 있으므로 도구 줄과 승인 카드가 다시
  그려진다
- 스트림이 끊기면(네트워크, 서버 재시작 아님) 마지막으로 받은 `seq` 로 한 번 다시 붙는다. 410 이면
  기록을 다시 읽는다
- 늦은 이벤트의 판정. 지금은 경로와 `session_id` 다. 여기에 `turn` 을 더한다
- 오른쪽 면의 머리글에 [멈춤]. 도는 턴이 있을 때만 켜진다
- Tauri 창을 닫을 때. `getCurrentWindow().onCloseRequested` 에서 도는 턴이 있으면 "도는 작업 N개가
  멈춘다" 를 묻는다. 권한은 `core:window:allow-close` 만 더한다. 브라우저는 묻지 않는다 — 탭을
  닫아도 서버와 턴은 산다

### 테스트

`test_main.py` 에 더한다. 모두 지금의 `work.py` 에서 빨강이어야 한다.

- 턴이 응답보다 오래 산다. `say` 의 응답 본문을 받자마자 버리고 수거한다. 대역 CLI 가 끝낸 뒤
  기록에 답 행이 있고, 잡음은 그때 풀린다
- 다시 붙기. 이벤트 몇 개 뒤 `events?after=k` 가 `k+1` 부터 `done` 까지 빠짐없이 준다. 끝난 턴에
  붙어도 같다. 다른 턴 id 는 410
- 멈추기. `stop` 뒤 `error` 이벤트가 오고, 잡음이 풀리고, 프로세스가 없다. 다음 `say` 가 같은
  CLI 세션 id 로 이어진다
- 두 화면이 한 턴을 꼬리 물면 둘 다 같은 순서로 받는다

## 승인 기록

### 지금

기록의 답 행은 도구 줄(`tools`)까지다(`tool/main/work.py:282`). 승인은 화면에만 있다가 새로 고치면
사라진다. 6단계가 "다시 열면 무엇을 허용했는지는 도구 줄로만 보인다" 로 미뤘다.

### 바꾸는 것

도구 줄과 승인을 한 줄에 순서대로 적는다. 답 행의 `tools` 를 `steps` 로 바꾼다.

```json
{"steps": [
  {"kind": "tool", "text": "Read · tool/lint.py"},
  {"kind": "approval", "tool": "Write", "text": "Write · tool/x.py", "answer": "allow", "by": "person"},
  {"kind": "approval", "tool": "Bash", "text": "Bash · pytest -q", "answer": "allow", "by": "session"},
  {"kind": "approval", "tool": "Edit", "text": "Edit · ../elsewhere.txt", "answer": "deny", "by": "outside"}
]}
```

| 칸 | 값 |
| --- | --- |
| `answer` | `allow`, `deny`, `none`(답이 오기 전에 턴이 끝났다) |
| `by` | `person`(사람이 눌렀다), `session`(세션 규칙이 답했다), `outside`(작업트리 밖이라 묻지 않고 거절), `read`(읽기 세션이라 거절) |

승인의 `input` 은 적지 않는다. `Write` 의 본문 전체가 기록에 쌓인다. 무엇이 쓰였는지는 작업트리와
커밋이 말한다.

- 사람이 답하면 버퍼에 `answered` 이벤트(`{id, allow, by}`)가 쌓인다. 다시 붙은 화면과 다른 창이
  카드를 답한 것으로 그린다
- 작업트리 밖과 읽기 세션의 거절은 지금 `tool` 줄이다(`chat_session.py:340`). 이것도 `approval` 에
  `by` 를 달아 낸다. 화면은 답이 끝난 카드로 그린다
- 옛 기록의 `tools` 는 `/api/work/log` 가 `steps` 로 바꿔 준다. 화면은 한 모양만 안다

테스트. 허용 하나, 거절 하나, 밖 하나, 답 없이 멈춘 것 하나의 턴을 돌리고 기록의 `steps` 가 그
순서와 값인지 본다. 새로 고친 화면의 모양은 `log` 의 응답으로 본다.

## 이 세션 동안 허용

### 규칙이 어디에 사나

`ChatSession` 이 쥔다. CLI 에 넘기지 않는다.

- Codex 의 `acceptForSession` 과 Claude 의 `permission_suggestions` 를 쓰면 CLI 가 더는 묻지 않는다.
  그러면 `_approval` 의 작업트리 밖 검사(`chat_session.py:338`)를 거치지 않는다. Claude 의 `Edit`
  허용 규칙은 경로를 가리지 않는다
- 두 호스트의 뜻도 다르다. Codex 의 명령 캐시와 Claude 의 규칙은 맞추는 방식이 다르다. 여기서 하나로
  정한다

`ChatSession._rules: set[tuple]`.

| 요청 | 규칙의 열쇠 |
| --- | --- |
| Claude `Edit`·`Write`·`MultiEdit`·`NotebookEdit` | `("file", 도구 이름)` |
| Codex `fileChange` | `("file", "fileChange")` |
| Claude `Bash` | `("command", "Bash", input.command)` |
| Codex `command` | `("command", "command", command, cwd)` |

`_approval` 의 순서는 이렇다. 작업트리 밖이면 거절한다. 읽기 세션이면 거절한다. 규칙에 맞으면
묻지 않고 허용하고 `by: "session"` 인 승인 이벤트를 낸다. 셋 다 아니면 사람에게 묻는다. 밖 검사가
규칙보다 앞이다.

### 수명

규칙은 `ChatSession.id` 에 붙는다. id 가 바뀌면 규칙도 없다.

| 일 | 규칙 |
| --- | --- |
| 모델·effort 바꾸기(`reconfigure`), 멈추기 | 남는다. 같은 객체, 같은 id |
| 문맥 비우기, CLI 바꾸기, 작업트리 지우기 | 없어진다. 새 객체 |
| 서버 재시작 | 없어진다. 디스크에 적지 않는다 |

### API 와 화면

- `/api/work/answer` 의 몸에 `scope: "once" | "session"` 을 더한다. 기본은 `once`. 읽기 세션이나
  밖 거절에는 `session` 이 올 수 없다(409)
- 승인 카드의 버튼은 [허용] [세션 동안] [거절]. 명령이면 가운데가 "이 명령은 세션 동안" 이다
- `/api/work/log` 가 `rules` 를 낸다. 머리글 아래 한 줄로 "세션 허용: Edit · Write · `pytest -q`"
  를 보이고 [해제] 하나를 둔다. `POST /api/work/rules/clear {path, session_id}`
- 4단계의 루프가 보내는 턴도 같은 세션이므로 같은 규칙을 탄다. 개요의 안전 경계와 같다

### 테스트

`test_agent.py` 의 대역 CLI 로.

- `Write` 를 `session` 으로 허용하면 다음 `Write` 는 묻지 않고 허용된다. `Edit` 는 묻는다
- 규칙이 있어도 작업트리 밖 `Write` 는 거절된다. 이 테스트는 밖 검사를 규칙 뒤로 옮기면 빨강이다
- `Bash` 의 `pytest -q` 를 허용하면 `pytest -q` 는 묻지 않고 `pytest -q -x` 는 묻는다
- Codex `command` 는 `cwd` 가 다르면 묻는다
- 새 `ChatSession` 은 규칙이 없다. `reconfigure` 뒤에는 남는다

## Codex 초점 세션을 `app-server` 로

### 지금

`ChatSession.app` 은 Codex 쓰기 세션만이다(`chat_session.py:151`). 초점 세션은 턴마다 `codex exec` 을
띄우고 `resume <id>` 로 잇는다(`chat_session.py:185`). 첫 턴의 기동이 매 턴 반복된다.

### 바꾸는 것

`app` 을 `is_codex and not isolated` 로 바꾼다. 초점 세션과 쓰기 세션이 한 갈래를 탄다.

| `thread/start` 인자 | 쓰기 | 초점 |
| --- | --- | --- |
| `sandbox` | `read-only` | `read-only` |
| `approvalPolicy` | `untrusted` | `never` |
| `developerInstructions` | 있으면 | 초점의 머리말 |
| 기동 인자 | 없음 | `--disable multi_agent`. 지금의 `exec` 과 같다 |

- 초점 세션에 승인 요청이 오면 지금처럼 `_approval` 이 묻지 않고 거절한다. `never` 라 올 일이 없지만
  길은 남긴다
- `exec` 갈래는 `explain` 전용으로 줄인다. `resume` 인자와 비격리 분기를 지운다. 격리 플래그는
  그대로 둔다
- 서버 재시작 뒤 옛 기록의 `session_id` 는 `exec` 이 만든 스레드 id 다. `thread/resume` 이 그것을
  받는지 실제로 본다. 받지 못하면 새 스레드로 시작하고 기록에 `context` 행 "Codex 이어가기 실패" 를
  남긴다. 조용히 대화를 잃지 않는다

### 확인할 것

- 위키 hook 이 `app-server` 초점 세션에서도 주입하는가. `exec` 은 사용자 설정과 hook 을 탔다.
  `hook_diagnostics` 의 기록으로 한 질문에 주입이 있었는지 본다. 없으면 이 이전은 멈추고 원인을
  적는다
- 두 번째 턴의 시간. 프로세스가 살아 있으므로 기동이 빠져야 한다. 전후를 한 번씩 잰다

## 토큰 수

### 지금

Claude 는 `done` 에 토큰과 비용을 싣는다. Codex `exec` 은 `turn.completed` 의 `usage` 를 싣는다.
Codex `app-server` 는 싣지 않는다(`chat_session.py:500`). 초점을 `app-server` 로 옮기면 초점도
토큰 수를 잃는다.

### 바꾸는 것

`thread/tokenUsage/updated` 알림을 받는다. `tokenUsage` 에 `last` 와 `total` 이 있다.

- `last` 가 턴 하나의 합인지 모델 호출 하나인지는 스키마가 말하지 않는다. 실제 알림으로 확인한다.
  턴 하나면 마지막 `last` 를, 호출 하나면 턴 동안의 `total` 차이를 쓴다
- `done.meta.tokens` 는 지금의 모양 그대로다 — `in`(`inputTokens`), `out`(`outputTokens`),
  `cache_read`(`cachedInputTokens`). `reasoningOutputTokens` 는 `reasoning` 으로 더한다
- 비용은 계산하지 않는다. Codex 는 구독 한도이고 요금을 곱할 표가 없다

화면. 질의 면은 이미 토큰을 그린다(`web/src/components/Stream.tsx:152`). 에이전트 면의 답 아래
줄(`Agent.tsx` 의 `Reply`)에 같은 모양을 더한다.

테스트. 대역 `app-server` 가 알림 둘을 보내면 `done` 의 `tokens` 가 확인한 규칙대로 나오는지 본다.

## 하지 않는 것

| 무엇 | 왜 |
| --- | --- |
| 위키 질의 턴의 재접속 | 답 하나가 짧고, 끊기면 다시 물으면 된다. 쉬운 설명과 번역이 한 스트림에 붙어 있어 나누는 일이 크다 |
| 앱을 닫아도 도는 턴 | 사용자의 결정. 트레이나 따로 뜨는 서버가 필요해지면 그때 계획을 쓴다 |
| 규칙을 디스크에 남기기 | 서버를 다시 띄우면 다시 묻는 것이 맞다. 세션 하나라는 약속이 그것이다 |
| 승인의 OS 알림 | 루프가 승인에서 멈추는 일이 생기는 4단계, 또는 화면을 다시 짓는 6단계에서 정한다 |
| 버퍼 크기 상한 | 턴 하나의 이벤트는 턴 마감 안에서 끝난다. 수만 개의 `delta` 가 실제로 보이면 묶는다 |

## 확인

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- 창에서. 쓰기 승인을 기다리는 턴을 두고 새로 고친다 — 카드가 다시 그려지고 [허용] 이 먹는다.
  도는 턴을 [멈춤] 으로 멈춘 뒤 다음 지시가 이어진다. [세션 동안] 으로 허용한 `Write` 가 다음에는
  `by: session` 으로 지나간다
- 실제 Codex 로 초점 한 질문, 쓰기 한 턴. 둘 다 `done` 에 토큰이 있다
- Tauri 창에서 도는 턴을 두고 닫기 — 물음이 뜨고, 취소하면 턴이 계속 돈다

## 단계

| # | 단계 | 무엇 | 상태 |
| --- | --- | --- | --- |
| 0 | 계획 순서 | `plans` 의 폴더 안 순서와 테스트 | 미착수 |
| 1 | 재접속 | `Run`·버퍼·`tail`, `events`·`stop`, 스레드가 쥐는 잡음, 테스트 | 미착수 |
| 2 | 승인 기록 | `steps`, `answered`, 옛 `tools` 변환, 테스트 | 미착수 |
| 3 | 세션 허용 | `_rules`, `scope`, 규칙 해제, 테스트 | 미착수 |
| 4 | Codex 이전과 토큰 | 초점의 `app-server`, `exec` 을 `explain` 전용으로, `tokenUsage`, 실제 Codex 확인 | 미착수 |
| 5 | 화면 | 다시 붙기, [멈춤], 세 버튼, 규칙 줄, 토큰, 창 닫기 확인 | 미착수 |
| 6 | 게이트 | 위 확인 전부 | 미착수 |
