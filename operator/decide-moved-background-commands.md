---
scope: operator
severity: landmine
repeat: rule
triggers: ["백그라운드", "고아", "background", "orphan", "TaskStop"]
slots: []
enforce:
  pretooluse: background_left.py
sources: []
sources_withheld: true
links: [hooks-fail-open, report-without-stopping]
---

# A command its timeout moved to the background is waited on or stopped

Rule. When a command outlives its timeout and the host moves it to the
background, the next step decides it. If its result is still needed, wait for
it or read its output. If it is not, stop it. Never go on to other work and
leave it running unread.

When it is broken. Nobody reads the command, and it keeps the machine busy for
as long as it runs. On 2026-11-25 a build turn's `du` over a uv cache was moved
this way. The turn went on to rebuild and test, and the walk kept the disk busy
until the owner noticed it in the cell. The turn's end would have killed it.
Until then it was an orphan that no step was going to read.

## How it is held

`tool/background_left.py` runs before every Claude tool call. It reads the
session's own transcript and lists commands a timeout moved
(`backgroundTaskId` with `timedOutAfterMs`). A command drops off the list when
the session waits (`Yield`, `Monitor`), reads it (`TaskOutput`, or a `Read`
naming it), or stops it (`TaskStop`), or when its task notice arrives. A
deciding call whose own result is an error decides nothing. While any command
is left on the list, only those deciding tools go through.

The first call reads the whole transcript; later calls read only what was
added, from a scan kept under the temporary folder, so a command moved long
ago stays on the list. A transcript replaced by another file, or changed in
its first 4 KB or in the 4 KB before where the last scan stopped, is read
again from its start. Beyond that the scan trusts Claude to only append.
Parallel decisions and notices settle by task: a failed call undoes nothing
another call decided. A command counts only while its output file is seen
held open for writing, which Windows shows. A reader looking at it does not
count, nor does one that keeps others from reading it, since that shows no
writer. A command whose process has ended holds
nothing, as when a resumed session's earlier process ended without a notice.
Elsewhere nothing shows a held file, so nothing is held.

## A GPU job the hook queued

A GPU job (`tool/gpu_job.py`) is wrapped into the machine's GPU queue, and on
Claude and DeepSeek the hook also sets `run_in_background`, since a queued job
can wait longer than any timeout. The agent did not choose that background
start, so it is held the same way as a command a timeout moved. It is found by
either record: a call whose input carries the runner's `gpu_run.py --hold`,
or a background start whose call did not ask for one. Its output lists the
queue's lines (`[gpu queue] ...`), and the job ends with the turn even if
nobody decides it. On Codex the wrapped job waits in the foreground and prints
the command that takes it out of the queue, so there is nothing to hold.

## What it does not do

It bounds no wait and shortens no timeout. A command the agent itself started
with `run_in_background` is a choice it already made, so the check leaves it
alone. Ending what a finished turn left behind belongs to that turn's
process job (`ChatSession`), not to this check.
