# 1단계 — 어긋남 정리

전체 설계와 단계의 관계는 [개요](0-overview.md)에 있다.

목표. 계획 문서, 주석, 코드가 서로 다른 말을 하는 곳을 없앤다. 뒤 단계가 새 계획 문서를 쓰면
SessionStart 가 그 남은 행을 에이전트에게 보여 준다. 그 판정이 틀려 있으면 다음 작업 초점(3단계)이
후보를 틀린 데서 뽑는다.

2026-09-25 에 계획 문서 11개와 `docs/verification.md`·`docs/quality.md` 를 전수조사하고 코드와
대조했다.

## 고칠 것

### 계획 상태 판정

`session_state.open_steps` 는 상태 칸이 정확히 `완료`·`취소` 일 때만 끝난 행으로 친다
(`tool/session_state.py:98`). `완료 — 양 호스트 실측` 처럼 뒤에 한 줄을 붙인 행은 남은 일로 센다.
2026-09-25 에 english-first-2 의 3행, english-first-3 의 8행이 그렇게 SessionStart 에 떴다. 그 시리즈는
이제 `done/` 아래라 읽히지 않지만, 같은 모양의 상태 칸은 앞으로의 계획에도 쓰인다.

고침. 상태 칸의 첫 낱말로 판정한다. `완료`·`취소` 로 시작하면 끝난 행이다. `미완료` 는 끝나지
않았다 — 앞글자 비교가 아니라 낱말 비교여야 하는 이유다.

테스트. `완료 — …`, `취소 — …`, `미완료 — 외부 조건으로 차단됨`, `미착수` 네 행의 표를 주고
남는 행이 뒤의 둘뿐인지 본다. 고치기 전 코드에서 빨강이어야 한다.

### 낡은 문서

| 어디 | 어긋남 | 고침 |
| --- | --- | --- |
| `docs/verification.md` | 공개 사본의 기록 그대로다. "다섯 채널", Slack 실행기가 나온다. wiki-agent 2–7단계의 검증은 없다 | 제목과 첫 절에 이 파일이 공개 사본 시절의 기록임을 적고, 지금의 검증은 `docs/plans/done/wiki-agent/7-verify.md` 를 가리킨다. 옛 기록은 지우지 않는다 |
| `docs/plans/done/wiki-agent/0-overview.md` 의 "넣지 않은 것" | 리뷰 셀이 "나중" 으로 남아 있다 | `loop` 4단계를 가리킨다 |

### 낡은 주석과 쓰지 않는 이름

Slack, 미러, `chat.handoff` 는 6단계에서 지웠는데 주석이 아직 그것을 이유로 든다.

| 어디 | 고침 |
| --- | --- |
| `tool/session_state.py:53` | `chat.handoff` 대신 지금 이 함수를 부르는 곳(`/api/draft`)을 이유로 |
| `tool/workspace/sessions.py:207` | "Slack brief" 를 뺀다 |
| `tool/test_sessions.py:188,216` | 미러가 아니라 지키는 소유 규칙을 이유로 |

`claude_session`·`codex_session`·`FINDERS` 는 미러가 유일한 호출자였다. 지금 부르는 곳은
테스트뿐이다. `sessions.py:7-8` 은 "소유 규칙을 테스트가 핀으로 박고 있어서" 남긴다고 적는다.
그 규칙이 `checkouts()` 의 것이라면 테스트를 `checkouts()` 로 옮기고 셋을 지운다. 옮길 수 없는
규칙이 있으면 남기고 이유를 그 규칙 이름으로 적는다. 어느 쪽인지는 테스트를 읽고 정한다.

### 작은 미룬 것

| 무엇 | 어디서 미뤘나 | 고침 |
| --- | --- | --- |
| `wiki-agent` 가 `위키-에이전트` 로 번역된다 | 7단계 | `glossary.toml` 의 `fixed` 에 넣는다. 캐시가 한 번 무효가 된다. 7단계는 "다른 이유로 고칠 때" 라 했지만, 기다릴 다른 이유가 없다 |
| 첫 채널 목록이 오기 전에 보낸 질의에 프로젝트 표시가 없다 | 6단계 리뷰 11 | `claimed` 가 비어 있으면 `/api/channels` 가 아닌 요청은 보내지 않고 기다린다(`web/src/lib/api.ts:96`). 서버도 쓰는 요청에서 `X-Project` 가 없으면 거절한다(`tool/main/app.py:86`) |
| 번역 요청의 선불 차감이 출력 토큰 상한이 아니다 | 3단계 PR #4 | `generationConfig.maxOutputTokens` 를 입력 길이에 비례해 건다(`tool/translate/__init__.py:371`). 차감한 금액이 실제 상한이 된다 |
| haiku 가 `chat-answer.md` 의 영어 지시를 어기고 한국어로 답한다 | 7단계 | 원인부터 본다. 사용자 단위 `CLAUDE.md` 의 언어 지시와 부딪치는지, `--append-system-prompt` 의 위치 문제인지. 원인이 이 저장소 밖이면 적어 두고 고치지 않는다 |

터미널의 변경이 작업트리 목록에 늦게 보이는 것(7단계)은 6단계로 넘긴다. 레일이 작업 단위로 바뀌며
목록을 다시 읽는 때가 같이 바뀐다.

## 하지 않는 것

- 따옴표 안의 글을 번역에서 지키기. 7단계의 결정대로 둔다
- 답변 이벤트 `hits` 에 심각도와 본문을 넣기. 4단계 문서가 "필요할 때" 라 했고 아직 필요한 곳이 없다
- 계정 고르기 화면. 5단계의 결정대로 둔다

## 확인

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- `session_state.plans()` 가 `done/` 의 문서를 읽지 않고, `loop/0-overview.md` 의
  2–7행을 낸다

## 단계

| # | 단계 | 무엇 | 상태 |
| --- | --- | --- | --- |
| 1 | 판정 | `open_steps` 첫 낱말 판정과 테스트 | 미착수 |
| 2 | 문서 | `verification.md`, wiki-agent 개요의 "넣지 않은 것" | 미착수 |
| 3 | 주석과 이름 | 낡은 주석, `claude_session`·`codex_session`·`FINDERS` 정리 | 미착수 |
| 4 | 작은 미룬 것 | 용어집, 프로젝트 표시 없는 요청, 번역 출력 상한, haiku 언어 원인 | 미착수 |
| 5 | 게이트 | 위 확인 전부 초록 | 미착수 |
