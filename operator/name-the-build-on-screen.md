---
scope: operator
severity: contract
repeat: rule
triggers: ["라이브 (확인|테스트|회차)", "화면(에서|에|을)? ?(확인|봐|보이|나오)", "재빌드", "새로고침", "(또|아직도) (실패|안 ?[되돼]|그렇게)", "직접 (확인|눌러|해 ?봐)", "이렇게 나(온|왔)"]
slots: []
sources: []
links: [run-inside-this-session, diagnose-from-what-ran, verify-narrow-then-wide]
---

# Handing over a screen starts with naming the build

Rule. **Before** asking the user to check something, write one line giving the
branch and commit of the build that is up. If the frontend is served
separately, name that bundle too. The request only works if the user can read
that line and decide for themselves that this is the thing they meant to look
at. Attach the same line to the failure report that comes back, so the run is
recorded against a build. Read that line off the server after starting it —
never from memory, and never from a `git` query, which is not guaranteed to be
what the server serves. Write `launcher_worktree` too; `unknown` means the
launcher did not say, not that the worktree is clean.

What goes wrong. **A run the user paid for gets thrown away.** A person
opening the app, having a conversation and writing up the result costs far
more than one test, and without a record of which code it was, none of it can
be used as evidence. Worse, recovering that answer means digging through logs,
leaving "what actually ran" — the thing [[diagnose-from-what-ran]] is about —
unrecorded on exactly the side where it cost the most.

## The server already holds that line — do not invent it

Read it after starting the server and copy it across. Do not write it from
memory: the commit just checked with `git` is not guaranteed to be the commit
the server is actually serving, and that gap is the failure this page is about.

Write `launcher_worktree` too. A correct commit does not describe the build if
the worktree is dirty. `unknown` means the launcher did not say, not that it
is clean.

What starts the server and where is held by [[run-inside-this-session]]. This
page is the step after: the moment it is handed to a person.
