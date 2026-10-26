The gate passed. Before the pull request goes up, write its text. Change
nothing and commit nothing in this turn.

## Who reads it

A colleague who joins next month. They know the product from the outside —
what a user sees and does — and nothing else: not the spec, not this part of
the code, not the words this task used. They read the pull request to decide
whether it is safe to merge and, months later, to learn why the code is the
way it is. If they have to open the code to understand a sentence, the
sentence failed.

## Where the text comes from

From the branch's final diff (`git diff <start>...HEAD`) and what you
verified. Read the spec only to learn the intent; never copy or translate its
sentences. A spec is a plan and a set of orders to you; the pull request
says what the finished change does.

## The block

Write one `pr-summary` fenced JSON block, in Korean, with exactly these keys:

- `title` — the outcome in plain words, at most 80 characters. Add
  `(동작 변화 없음)` when nothing observable changes.
- `context` — one or two sentences on what this part of the product does,
  for someone who has never seen it. Name the feature by what it does for a
  user (`컴패니언이 먼저 말을 거는 기능`), not by a module or function.
- `changes` — two to five lines, each a difference the reader can picture.
  One line per idea, not one per file.
- `behavior` — what a user or a caller notices, before → after. When it is
  a refactor, say `없음` and what stays the same.
- `why` — one or two sentences: the problem this removes or the risk it
  closes.
- `verify` — how you know it works, in plain words: what the tests pin, what
  you ran and saw. One line each.
- `code` — two to six lines for the reader who opens the code next. Each line
  is a backticked name that appears in the diff, then what it does.

## Rules the server checks

- Every prose field (all but `code`) is Korean and holds no code name at all:
  nothing in backticks, no identifier with `_`, no file name, no path. Say
  what the thing does instead. Code names go only in `code`.
- Every backticked name in `code` appears in the diff.
- Describe, never instruct: no `하십시오`, `하라`, `할 것`.
- Write in the plain past or present (`합쳤다`, `기록한다`), one voice for the
  whole block.

A summary that fails comes back to you once with the reasons. A second
failure goes up as files and checks only.

## Before you send it, check

1. Could someone who has never seen this code say back, from `context` and
   `changes` alone, what changed? If a sentence needs a term they would not
   know, explain the term or drop it.
2. Does `behavior` answer "will anyone notice?" in one line?
3. Is every domain word explained at its first use?

## What not to write

This passed every format check and still failed its reader (#262). Every
sentence narrates code; a newcomer cannot tell what the feature is or
whether anything changed:

```
proactive_check와 proactive_trigger에 각각 있던 정책 평가·화면 근거에 따른
차단·최종 결과 기록을 _evaluate_proactive_eligibility로 모았습니다.
입력 변환과 준비된 타이밍 값 전달은 그대로이며, record_check는 확인
경로에서만 공통 판정 직전에 호출됩니다.
```

## Three examples: spec, then the pull request text

### 1. A refactor — nothing observable changes

The spec:

```json
{"goal": "Extract the duplicated eligibility sequence in proactive_check and proactive_trigger into one private helper; keep record_check in the check path only.",
 "done": ["pytest", "Both endpoints keep evaluate → vision veto → record order"],
 "decisions": [{"what": "Keep record_check outside the helper", "why": "trigger must not stamp a check time"}]}
```

The diff: `routes_proactive.py` (+33 −36), two new tests in
`test_proactive_routes.py`, one line in the architecture notes.

```pr-summary
{"title": "선제 발화 자격 판정을 한 곳으로 모음 (동작 변화 없음)",
 "context": "컴패니언은 사용자가 말하기 전에 먼저 말을 걸 수 있다(선제 발화). 말을 걸기 전에 '지금 걸어도 되는가'를 판정하는데, 이 판정을 미리 확인만 하는 요청과 실제로 말을 거는 요청 두 곳에서 한다.",
 "changes": ["두 요청이 같은 판정 순서(사용자 설정 확인 → 화면 상황 때문에 거절 → 결과 기록)를 각자 복사해 갖고 있던 것을 하나로 합쳤다.", "미리 확인하는 요청만 '마지막으로 확인한 시각'을 남기는 차이는 그대로 두었다."],
 "behavior": "없음. 두 요청의 응답과 남기는 기록은 바뀌기 전과 같다.",
 "why": "같은 판정이 두 벌 있어서, 한쪽만 고치면 확인 결과와 실제 동작이 어긋날 수 있었다.",
 "verify": ["기존 테스트는 한 줄도 바꾸지 않았고 모두 통과했다.", "두 요청이 같은 입력에 같은 판정과 같은 기록을 내는지, 설정 거절과 화면 거절이 겹칠 때 설정 쪽 사유가 남는지 보는 테스트 2개를 추가했다."],
 "code": ["`_evaluate_proactive_eligibility` — 합쳐진 판정 함수", "`record_check` — 확인 요청에서만 부르는 시각 기록, 판정 함수 밖에 둔다", "`test_check_and_trigger_preserve_eligibility_inputs_and_recording_order` — 두 요청의 판정 입력과 순서를 고정"]}
```

### 2. A bug fix — users notice

The spec:

```json
{"goal": "After login, redirect to the page the user came from instead of /home; validate next= against same-origin paths.",
 "done": ["pytest", "Open redirect to another origin is refused"],
 "decisions": [{"what": "Keep the target on the server session", "why": "a query string can be forged"}]}
```

The diff: `auth/login.py` (+21 −4), `auth/session.py` (+9), three tests.

```pr-summary
{"title": "로그인하면 보던 페이지로 돌아간다",
 "context": "로그인이 필요한 페이지를 열면 로그인 화면으로 보내고, 로그인이 끝나면 다시 서비스로 들여보낸다.",
 "changes": ["로그인 화면으로 보낼 때 원래 열려던 주소를 서버 쪽 세션에 적어 두고, 로그인이 끝나면 그 주소로 돌려보낸다.", "다른 사이트 주소는 돌아갈 곳으로 받지 않는다. 그런 주소가 오면 첫 화면으로 보낸다."],
 "behavior": "전에는 로그인 뒤 항상 첫 화면으로 갔다. 이제 보던 페이지로 돌아간다.",
 "why": "공유받은 링크를 열면 로그인 뒤 그 링크를 다시 찾아 들어가야 했다.",
 "verify": ["로그인 뒤 원래 페이지로 돌아가는 경우, 다른 사이트 주소가 거절되는 경우, 돌아갈 주소가 없는 경우를 테스트 3개로 확인했다."],
 "code": ["`remember_target` — 돌아갈 주소를 세션에 적는다", "`safe_target` — 같은 사이트 안의 주소만 통과시킨다", "`login_done` — 로그인 성공 뒤 돌려보낼 곳을 고른다"]}
```

### 3. A new setting — callers notice

The spec:

```json
{"goal": "Add a configurable synthesis timeout (tts.timeout_seconds, default 8) and fall back to the cached greeting when it expires.",
 "done": ["pytest", "A slow synthesizer yields the fallback within the timeout"],
 "decisions": [{"what": "Default 8 seconds", "why": "p99 synthesis is 5.1s in the last week of traces"}]}
```

The diff: `tts/synth.py` (+27 −3), `config/defaults.toml` (+2), two tests.

```pr-summary
{"title": "음성 합성이 늦으면 8초 뒤 준비된 인사말로 대신한다",
 "context": "컴패니언의 대답은 글로 만든 뒤 음성으로 합성해 들려준다. 합성 서버가 느려지면 지금까지는 사용자가 아무 소리도 없이 기다렸다.",
 "changes": ["합성에 시간 제한을 두었다. 기본값은 8초이고 설정 파일에서 바꿀 수 있다.", "시간이 넘으면 미리 녹음해 둔 짧은 인사말을 대신 들려주고, 원래 대답은 버린다."],
 "behavior": "합성이 8초를 넘을 때만 달라진다. 침묵 대신 준비된 인사말이 나온다. 보통 속도에서는 그대로다.",
 "why": "지난주 기록에서 합성이 멈춘 듯 길어진 경우가 있었고, 그동안 사용자는 앱이 멈춘 줄 알았다.",
 "verify": ["일부러 느리게 만든 합성기로 8초 안에 인사말이 나오는지, 정상 속도에서는 원래 대답이 나오는지 테스트 2개로 확인했다."],
 "code": ["`synthesize` — 시간 제한을 걸고 넘으면 대체 음성을 돌려준다", "`timeout_seconds` — 설정 파일의 새 값, 기본 8"]}
```

## Now

Write the `pr-summary` for this branch the same way. Send only that block;
the report is already in.
