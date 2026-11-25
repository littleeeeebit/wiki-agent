## The real measurement this build must be ready for

After the gate passes, the server opens the run's first pull request as a
draft. Then it runs the product on the owner's PC against the real services,
with the real accounts and addresses the owner gives. The review reads that
measurement. A smoke test, a fake service or a recorded answer is not a
measurement; nothing stands in for a real call. Commit `.wiki/measure.json`
with these five parts, and the code each part names.

The server measures by itself first, as many times as it takes. Every metric
that needs no person must pass in its own measurement, the live broadcast
included; only the metrics that failed come back to you. Then the owner
measures once, on their own screen, watching and listening. `ONESHOT_MEASURE`
tells the script and the product whose measurement it is: `auto` for the
server's, `owner` for the owner's.

In a run that builds a PC app (`desktop`), the measurement runs the program
the owner will install, never a headless server. So this turn also writes the
program's first screen: an address box, start and stop, the video or
broadcast playing with the converted sound, and the speakers found. It is the
screen a person uses for the measured flow, nothing more; later stages build
the other screens on it. The program's process serves the same local API the
script drives, so the window shows what the script does.

1. `serve`, or `app` in a `desktop` run (never both).
   - `serve`: `{"command": "…", "health": "http://127.0.0.1:<port>/<path>",
     "seconds": 120}`. The command starts the whole backend headless: its API
     server, its workers, and the real sign-in flow. It keeps running until
     the server stops it.
   - `app`: `{"run": {"folder": "…", "program": "….exe", "args": […],
     "cwd": ".", "env": {…}}, "health": "http://127.0.0.1:<port>/<path>",
     "seconds": 120}`. `run` is the unpackaged debug build the gate makes,
     shaped as the PC app's `run`: a folder inside the worktree, the program
     inside it, and at least one argument or variable that puts the program's
     data at `{data}`. Everything the program keeps goes under `{data}`: its
     settings, a sign-in, and any runtime or model it downloads. The server
     keeps that folder from one build to the next, so a download or a sign-in
     is done once. The server freezes the folder and starts the program in a
     job of its own, on the owner's screen, for `auto` as for `owner`: the
     owner sees its window and hears its sound in every measurement, so the
     program plays through the speakers as it would for them, and what it
     says also goes to the clips. Nothing else the measurement starts opens
     a window: the script and every helper it starts run without a console
     window, and only the program's own window is seen.

   Either starts even when the owner has not given the inputs yet, and
   `health` answers 200 once it can take requests. The server starts it at
   the end of every build check. One that does not come up fails the check.
2. `measure`: `{"command": "… {out} …", "minutes": 15}`. A script, committed
   here, that drives the running backend through the requirements as a person
   would, against the real services. It reads the inputs from their
   environment variables. It writes `{out}/report.json`:
   `{"metrics": {"<id>": <number or true/false>}, "notes": "<Korean>"}`. A
   metric is read from what the product actually put out, never from the
   product's account of what it did: the sound it played or recorded, the
   text on its screen, the file it wrote. A share of speech converted into
   Korean is measured by recognising the language of the recorded output,
   not by subtracting the spans the program listed as unconverted; a program
   that played the original and listed nothing would otherwise pass at 1.
   A metric with nothing to measure (no speech heard, no speaker found) is
   a failure, never a pass. And,
   when `clips` is true, a few short converted samples under `{out}/clips/`
   (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.mp4`, `.webm`) for the owner to listen
   to. It never waits for a person when `ONESHOT_MEASURE` is `auto`: it uses
   the sign-in `{data}` already holds, and a metric it cannot reach without a
   person it leaves out of the report. When an input itself is wrong, it writes
   `{"input_error": {"input": "M2", "why": "<reason>", "checked": {"how":
   "<Korean>", "answer": "<Korean>"}}}` instead. Examples: a key the service
   refuses, a broadcast that is not live, a sign-in the owner did not finish.
   The owner gave that input, so the server does not take the script's word
   for it: an `input_error` comes back to you as a failed measurement.
   `checked` is the check made outside the program's own code that showed
   the input wrong (`how`) and what the service answered (`answer`), each
   in Korean: for a broadcast, the platform itself saying it is not live now.
   Only an `input_error` with such a `checked`, after you were sent it,
   reaches the owner, who reads it. It never prints a secret.
3. `inputs`: what only the owner can give, each
   `{"id": "M1", "what": "<Korean>", "kind": "secret" | "text", "env": "NAME",
   "guide": [steps]}`. A secret goes to Windows Credential Manager. A `text`
   is an ordinary value, such as a live broadcast's address. An input whose
   `env` is one of the design's needs shares that need's stored value.
4. `during`: the steps the owner takes while the script runs, such as
   choosing a Google account in the browser window it opens. Use the same
   step shape, and name in each step's `metrics` the metrics that step is
   needed for. The measurement screen shows nothing the script prints or
   writes while it runs, and the owner never needs to read its console
   output; a saved result keeps a masked tail of that output beside the
   report and the clips. When a step needs the owner to read
   something, such as a device code, the script opens it for them in the
   default browser, on a page the backend or the script serves on
   `127.0.0.1` that shows it. That step has no `url`, since the script
   opens the page itself, and no `copy`. One step per browser page, as
   everywhere: the step on that page says how to carry what it shows to the
   next page, and the next step says where it goes there. The example below
   is only the code handoff: the real `during` steps go on until the
   service has authorized the device, through any sign-in and every consent
   screen, and each step's `see` covers both a browser already signed in
   and one that is not.

   ```json
   [{"window": "브라우저 — 기기 코드 (실측이 열어 준 창)",
     "do": "창에 보이는 기기 코드를 적어 두거나 「복사」를 누른다. 그다음 같은 창의 「Google 기기 연결 열기」를 누른다.",
     "see": "새 탭에 Google 의 기기 연결 화면이 열리고, 코드를 넣는 칸이 보인다."},
    {"window": "브라우저 — Google 기기 연결", "url": "https://www.google.com/device",
     "do": "코드 칸에 앞 창의 기기 코드를 넣거나 붙여 넣고 「다음(Next)」을 누른다.",
     "see": "이미 로그인했으면 연결할 Google 계정을 고르는 목록이, 아니면 Google 로그인 화면이 보인다."}]
   ```
5. `metrics`: each
   `{"id": "live_delay_s", "what": "<Korean>", "unit": "초",
   "requirements": ["R8"], "calls": ["X2"], "pass": {"op": "<=",
   "value": 65}}`. A metric that needs a `during` step, directly or through
   another (a sign-in, a link made by a device code, a setting changed on
   the linked screen), carries `"person": true`; every other metric is
   judged in the server's own measurement. Together they reach every outside
   call of the design
   (`calls`), so a real Google sign-in or a real live broadcast is in the
   measurement, not beside it. Set `"clips": true` whenever the product makes
   something a person hears or sees.

## The guides the owner follows

The owner is not a developer. They follow each input's `guide` and each
`during` step exactly as written, one window at a time. Write each step in
Korean, as plainly as you would explain it to someone using that service for
the first time:

```json
{"window": "브라우저 — Google Cloud 콘솔", "url": "https://console.cloud.google.com/auth/overview/personal",
 "do": "왼쪽 위 「프로젝트 선택」을 누르고 「새 프로젝트」를 누른다. 이름 칸에 vtuber-ko-dub 를 넣고 「만들기」를 누른다.",
 "see": "화면 위쪽에 vtuber-ko-dub 가 선택된 프로젝트로 보인다."}
```

- `window` names the program and the page the owner is looking at.
- `do` names the exact button, menu or field, as the screen labels it. Give
  the Korean label, and the English one in parentheses when the service is
  often shown in English. Name only one action, or a few that happen on the
  same screen.
- `see` says what appears after the action, so the owner knows the step
  worked before going on.
- `url` opens the page directly, where one exists. It is an `https://`
  address; a page the script opens on `127.0.0.1` has none.
- The last step of an input's guide names in `copy` exactly what to copy
  from that screen, for example `"클라이언트 ID"`. The owner pastes it into
  this input's box in the wiki-agent window. A `during` step has no box to
  paste into: it says in `do` where what it shows goes.
- Start from nothing: signing in, creating the project, turning on an API,
  any consent or test-user setting the call needs. Leave nothing to "set it
  up as usual". Where a value is shown only once (an OAuth client secret is),
  say so in the step that shows it.
- Check the current screens in the service's official documentation before
  writing a step. For a Google sign-in a desktop app makes,
  `https://console.cloud.google.com/auth/overview/personal` (Personal mode)
  sets up the project and a Desktop app client in one page. It shows the
  Client ID and Client secret once, in its "OAuth client created" dialog.
  Only the account that made it can sign in.

## When this turn ends, also

5. `.wiki/measure.json` passes the server's check, which names what is
   missing.
6. `serve.command`, or the program `app` names, comes up and answers
   `health` with 200 on the committed HEAD.

## When the review sent this build back

A finding you change: fix it where it points. A finding you do not change,
because the fix would break another requirement or the finding is wrong:
commit a new test that pins the behaviour you defend, and answer it in exactly
one fenced block:

```oneshot-answers
[{"area": "<the finding's area, word for word>", "location": "<the finding's location, word for word>", "evidence": "<Korean: why the code is right, naming the lines and any measured value>", "breaks": ["R12"], "test": "worker/tests/test_ingest.py::test_close_delimited_body_is_failed"}]
```

`breaks` names the requirements a change would break, and `test` names the
file and test you added in this turn (`path::name`, or `path::Class::name`
inside a class). It must be a test the refused HEAD did not define: editing
an existing test does not count, and the build's own test run must pass it. Either way the turn ends on a new
commit: a HEAD already refused is never reviewed again, and a turn that adds
no commit is sent back. The review reads your answers. When you and the review
still disagree in an area, the owner decides between you.
