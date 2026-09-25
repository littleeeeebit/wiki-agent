---
scope: craft
severity: landmine
repeat: rule
triggers: ["원인", "왜 (깨|실패|안 되|안 돌)", "진단", "디버깅|디버그", "회귀|regression", "갑자기", "되던 게", "이상하다"]
slots: []
sources: []
sources_withheld: true
links: [hooks-fail-open, run-inside-this-session, ask-with-arrow-key-options, verify-narrow-then-wide, error-names-the-symptom-site]
---

# Ask what actually ran, first

Rule. When something breaks, query what actually executed before guessing at
the cause. Which image came up, which setting was read, which process
answered. Without that query, do not form a hypothesis. Having formed one, do
not state it as a conclusion or act on it until it is confirmed.

What goes wrong. **The hypothesis drives an action, and the action does damage.**

What a command kills is not written in the command's name. `wsl --shutdown` is
not "free some memory", it is "stop every container on this machine", and that
includes other projects'.

### The one line that stops someone else's from being called an orphan

```bash
docker inspect <name> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

The name does not tell them apart. The label does. A container with no label
has to be matched by its published ports against that project's configuration.

## Two places where plausibility looks like evidence

Timing that lines up is not grounds.

A risk warned about in advance is the most dangerous of all. An observation
that agrees with one's own forecast is not evidence; it is the best possible
place for confirmation bias to attach.

## Do not invert the order

Written in the tone of a conclusion, the next step gets built on it. The user
reads that sentence and decides, and the agent then acts consistently with it.

## What to run first

Whatever layer is suspected, there is usually a query that returns what
actually ran. All of them are read-only, and all of them are cheaper than a
hypothesis.

| Suspected | Instead of guessing |
| --- | --- |
| Which image or container came up | `docker events` · `docker inspect` · `docker ps -a` |
| Which setting was read | Print the value itself — not the file, **the value the process read** |
| Which process holds that port | `CommandLine`, not the name — [[run-inside-this-session]] |
| Which code ran | Print once at that spot and revert it |
| What failed | The **whole output** into a file, not a truncated summary |

## The same discipline as doubting a green

Deciding the cause of a red without confirming it is the same failure as
trusting a green without confirming it. Both treat something unconfirmed as
confirmed.

[[hooks-fail-open]] carries the conclusion in one sentence: worse than
something not running is something believed to be running. In diagnosis it
reads as — worse than not knowing is believing you know. Believing you know
leads to acting, and the action has to be undone.

## The trigger is half of it

The regex here only fires when the user says out loud that something broke.
The real failure point is the moment a hypothesis forms, and that does not
appear in an utterance — [[ask-with-arrow-key-options]] wrote the same limit
on its own page, for the same reason: a layer-2 hook only sees what the user
typed.

So the other half of this page is a habit. If "what actually ran" has not been
asked once, the diagnosis has not started.

## An error message is not an execution record either

A name like `X_missing` says where a lookup failed and nothing about what that
lookup was looking at. Reading the name as an execution record produces
exactly the misdiagnosis described here —
[[error-names-the-symptom-site]].
