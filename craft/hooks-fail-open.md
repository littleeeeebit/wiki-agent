---
scope: craft
severity: landmine
repeat: rule
triggers: ["훅", "hook", "cp949", "UnicodeEncodeError", "UnicodeDecodeError", "인코딩", "settings\\.json", "주입", "안 (뜨|붙|실)"]
slots: []
sources: []
sources_withheld: true
links: [english-progress, diagnose-from-what-ran]
---

# A hook never stops the session, whatever happens

Rule. A hook script turns every exception into a pass at its entry point, and
pins the encoding of `stdin` and `stdout` to UTF-8 itself. Whatever the
environment hands it, a hook failing must not make that session unusable.

**Pinning the encoding is not a hook rule — it covers all of `tool/*.py`.**
What kills the process is not being a hook; it is being Python with a pipe on
stdout, and a CLI runs on a pipe too. When a rule's name narrows its scope,
the place it narrowed to is the next incident.

The entry-point guard is for hooks alone. A hook has to swallow, because its
failure stops the session; a CLI has a person reading the result, and there a
traceback is the answer.

What goes wrong. The hook disappears quietly. Worse than the automation not
running is **it not running while everyone believes it did**.

## How to hold it

- Catch at the entry point. A `try` inside `main` only covers what is inside.
- Write the exception's name to stderr and nothing else. Korean in the message
  kills that stderr write in turn. A type name is ASCII so it survives, and
  `UnicodeEncodeError` alone is enough to diagnose from.
- Fixing `stdout` alone is half of it. With `stdin` broken, the utterance does
  not match the triggers and the hook **neither dies nor does anything**. Not
  even a traceback is left, which makes it harder to find.
- Return exit code 0. A CLI marks any non-zero code as a failure, and a failed
  hook leaves noise on the person's screen.

## The third face — reading someone else's stdout

The first two are the stdout this writes and the stdin this reads. The third
is `subprocess.run(..., capture_output=True, text=True)` — reading a child's
stdout, whose decoding the locale also decides. A child emitting UTF-8 kills
the parent reading cp949 with `UnicodeDecodeError`.

`sys.stdout.reconfigure` cannot stop this. That is this process's stream, not
the pipe.

Where it dies is different too. The exception is raised in `subprocess`'s
reader thread, that thread dies quietly, `run.stdout` becomes `None`, and the
body blows up on an unrelated line, raising
`TypeError: argument of type 'NoneType' is not iterable`.
The first place an encoding incident does not look like one.

```python
subprocess.run(cmd, capture_output=True, text=True,
               encoding="utf-8", errors="replace")
```

`errors="replace"` is part of the same fix. The aim is that whatever the child
emits, the parent survives, and a few broken characters do not block a
diagnosis.

The condition is not where the file lives. It is Python attached to a pipe,
and a throwaway script attaches to a pipe too.

## There is a check now — `lint`'s "encoding not pinned"

`tool/lint.py::fragile_tools` compares the actual output calls in the AST
against the UTF-8 pinning. `fragile_io` looks at whether tools that read stdin
pin UTF-8, and whether operational tools' text subprocesses carry
`encoding="utf-8", errors="replace"`. A byte pipe has no decoding, and a
test's strict decoding is a check that finds wrong output, so `replace` is not
forced there.

`test_lint.py` holds that ground — it plants a tool whose name exists only in
a string and watches the check turn red. A place where a check cannot see
itself is where *the belief that a check exists* survives the longest.

The entry-point guard is checked by `missing_hook_guards`. It covers the
shared event hooks and the PreToolUse hooks pages declare; it is not an
analyser that proves every control flow. `test_wiki_health.py` removes lint's
and repo_lint's own output pinning, stdin pinning, child-output policy and
entry-point guard from a temporary copy and confirms the check turns red.

## Installation and execution are confirmed separately

The hub's code and pages are read by absolute path, so an edit applies from
the next run. New events, hooks, Claude deny rules and command options are
copied into the settings, so those need `apply --write`. The target adapter's
`agents` declares which agents are expected. Even if the whole settings file
disappears, the wiring checks in `lint` and `repo_lint` find it. `sync`'s Stop
diagnosis uses the same checks.

After updating the hub, run `apply --check` for both agents in that
repository. Where it differs, update with that agent's `--write` and check
again. The hub is no exception. The hub gate includes a real settings check.
The hub does not quietly write a target's settings. The gate's `lint --check`
excludes only slot-value differences from the exit code, and fails on real
page, source and wiring defects. `apply`'s change preview exits 0 by default,
so a gate always uses `--check`.

A green wiring check is not evidence that the host ran the event. Codex's
trust and activation, and event delivery in a real session, are confirmed
separately. `trajectory.jsonl` is a trace of the injector running; it does not
identify another event's success or the session host. Record a direct
invocation and a real event as different things.

`hook_diagnostics.py` keeps only late runs and forced terminations, and
deletes records of fast clean exits. `watch_hook_timeouts.ps1` likewise sees
only slow processes while they run. Neither catches "not installed", "never
ran", or a fast run that injected nothing. Do not read an absence of records
as a healthy hook.

## The same sentence is used in diagnosis

This page's conclusion — worse than something not running is something
believed to be running — is not a rule about automation alone. The same shape
appears wherever the cause of a break is decided without confirmation: not
knowing, believing you know, and acting on it. [[diagnose-from-what-ran]] is
that face of it.
