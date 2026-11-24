## The real measurement this build must be ready for

After the gate passes, the server opens the run's first pull request as a
draft. Then it runs the backend on the owner's PC against the real services,
with the real accounts and addresses the owner gives. The review reads that
measurement. A smoke test, a fake service or a recorded answer is not a
measurement; nothing stands in for a real call. Commit `.wiki/measure.json`
with these five parts, and the code each part names.

1. `serve`: `{"command": "…", "health": "http://127.0.0.1:<port>/<path>",
   "seconds": 120}`. The command starts the whole backend headless: its API
   server, its workers, and the real sign-in flow. It keeps running until the
   server stops it. It starts even when the owner has not given the inputs
   yet, and `health` answers 200 once it can take requests. The server starts
   it at the end of every build check. A backend that does not come up fails
   the check.
2. `measure`: `{"command": "… {out} …", "minutes": 15}`. A script, committed
   here, that drives the running backend through the requirements as a person
   would, against the real services. It reads the inputs from their
   environment variables. It writes `{out}/report.json`:
   `{"metrics": {"<id>": <number or true/false>}, "notes": "<Korean>"}`, and,
   when `clips` is true, a few short converted samples under `{out}/clips/`
   (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.mp4`, `.webm`) for the owner to listen
   to. When an input itself is wrong, it writes
   `{"input_error": {"input": "M2", "why": "<Korean>"}}` instead. Examples: a
   key the service refuses, a broadcast that is not live, a sign-in the owner
   did not finish. It never prints a secret.
3. `inputs`: what only the owner can give, each
   `{"id": "M1", "what": "<Korean>", "kind": "secret" | "text", "env": "NAME",
   "guide": [steps]}`. A secret goes to Windows Credential Manager. A `text`
   is an ordinary value, such as a live broadcast's address. An input whose
   `env` is one of the design's needs shares that need's stored value.
4. `during`: the steps the owner takes while the script runs, such as
   choosing a Google account in the browser window it opens. Use the same
   step shape.
5. `metrics`: each
   `{"id": "live_delay_s", "what": "<Korean>", "unit": "초",
   "requirements": ["R8"], "calls": ["X2"], "pass": {"op": "<=",
   "value": 65}}`. Together they reach every outside call of the design
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
- `url` opens the page directly, where one exists.
- The last step names in `copy` exactly what to copy from that screen, for
  example `"클라이언트 ID"`. The owner pastes it into this input's box in the
  wiki-agent window.
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
6. `serve.command` comes up and answers `health` with 200 on the committed
   HEAD.

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
