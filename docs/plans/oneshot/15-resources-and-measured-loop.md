# Step 15 — The machine is shared, and only the server measures

The overall design is in the [overview](0-overview.md). Until this step an
agent knew nothing about the machine it ran on. It could start a second GPU
job beside its own, point its own program at the owner's real data folder,
and report numbers from a run nobody could see. Each of these was written
down as a rule at some point, and each was ignored.

Goal. Hard limits, not prompt lines, keep an agent from overloading the
machine and from touching what belongs to the owner. The build's repeated
measurements are the server's, run on the real program, and the owner
watches them only when they want to.

## Why this step exists

| What happened | What it showed |
| --- | --- |
| `vtuber-ko-dub`, 2026-11-26 10:00–11:00: the build agent left its own `vtuber-serve.exe` holding the GPU after its run ended, started a GPU analysis beside it, waited, and Claude Code stopped the analysis at its 30-minute cap on a moved command. It then stopped the backend and ran the analysis again | An agent does not see what holds the GPU, its own idle process included. About an hour was lost |
| The same turn ran its own end-to-end runs four times (`Temp\vkd\m13`–`m16`) on a headless debug backend with `--data-dir %LOCALAPPDATA%\vtuber-ko-dub` | The owner's real data folder was written by a build turn. The 0.78 it reported to the owner came from no run of the real program; the server's measurement of the same video said 0.39 |
| Agents cram tests into a machine already busy with other repositories' work | Without the machine's size and load in front of it, an agent plans as if it had the machine to itself |
| Prompt lines about all three already existed in some form | The owner: an agent often does not read a prompt, or reads it and does not follow it. A rule that matters is enforced |

## Decided with the owner (2026-11-26)

| Question | Decision |
| --- | --- |
| Prompt or enforcement | Both. The prompt says it, and a check makes it so |
| What an agent knows | The machine's CPU, RAM, GPU and disks, and at each run the load now: CPU, RAM, VRAM and what holds the GPU |
| A heavy command that cannot run now | It waits in a queue rather than failing |
| Orphans from queueing | Impossible by construction: Windows ends what a dead queue runner started, and a turn's place in the queue dies with its process |
| Waits | No wait or timeout is shortened anywhere ([moved commands](../../../operator/decide-moved-background-commands.md)) |
| Who measures | Only the server, on the real program. The build agent never runs the measurement script itself |
| Where the owner sees it | The build's repeated measurements run on a screen of their own; the owner watches one live from the measurement card when they want, and the clips stay. What the owner must check by hand still opens on the owner's screen |
| How it ships | Two pull requests, 15a and 15b, in that order. Neither is split further, and nothing outside its task list goes into it |

## 15a — the machine's size, its load, the GPU queue, the owner's data

This applies to every Claude and Codex session wired to this hub, not only
oneshot runs, except where a line says a build session.

### The machine, once a session starts

`tool/machine.py` reads the CPU model with its cores and threads, the RAM in
GB, each GPU's model and VRAM, and each disk's model, type (SSD, HDD) and
bus. It reads them once per boot and keeps them under the temporary folder.
`session_state` adds one line with them to the session's opening context.
A value it cannot read is left out of the line, never guessed.

### The load, at each run

The same file reads the load now: CPU percent, RAM used and total, VRAM used
and total (`nvidia-smi`), and the processes the GPU lists, at most once every
two seconds. A shell call that starts a program (below) gets that line as
context before it runs. Inspection commands (`grep`, `git`, `ls`, reading a
log) get nothing, so the line stays rare.

Windows does not show per-process VRAM (`nvidia-smi` answers N/A), so the
line names the holders and the total, not each holder's share.

### Which commands start a GPU job

A shell call's program words are read as `guard` already reads them. A word
counts as a GPU job when it is `python`, `python3`, `py`, `uv run`, `uvx`,
`pytest`, or an `.exe` inside the session's repository, its worktrees or the
temporary folder. This is a reading of the text and can miss a job. The
checks below miss it only as long as nothing of this session holds the GPU.

### This session's own GPU holder

A session's own processes are those in the Windows job its hook runs in
(the app's `ChatSession` job), so a process whose parent already exited
still counts. Outside a job they are the descendants of the Claude or Codex
process. When one of them is on the GPU's list and the call starts a GPU
job, the call is denied. The reason names the pid, the program and its
command line, and says to stop it or wait for it. A queue behind one's own
process never moves when that process is an idle server, so this case never
queues.

### The queue

A GPU job behind another session, another repository or the server's
measurement waits in a queue instead of failing.

1. `command_repair` wraps the command in `tool/gpu_run.py`. When the GPU is
   free at that moment, the call runs as it was asked, in the foreground.
2. When it is not, the call is also changed to `run_in_background`, with a
   context line saying its place in the queue and that a notice comes when
   it ends. Claude Code's 30-minute cap applies to a command its timeout
   moved, not to one started in the background, and no foreground timeout
   counts the wait.
3. The turn's place is a file under `%TEMP%\wiki-agent-gpu\queue`, held open
   by the waiting runner with no sharing. The GPU's lease is one more such
   file, held by the runner that runs. Windows closes both the moment their
   process ends, however it ends, so no lease or place outlives its owner.
   Places are served in the order they were taken; one whose file can be
   opened belongs to no live runner and is passed over.
4. Before it starts the command, the runner also waits while free VRAM is
   under 20 % of the total or free RAM under 15 %, saying so in its output.
   This covers what holds the GPU outside the queue: a game, another
   program, a session the hub does not reach.
5. The server's measurement takes the same lease before it starts the
   program and keeps it until the program has ended.

### No orphan from the queue

- The runner starts the command inside a job of its own that kills every
  member when the job closes (`common.process.contained`). TaskStop, a kill
  from outside and Claude Code's cap all end the runner, and with it
  everything it started.
- The runner holds the handle of the session's Claude or Codex process,
  named by the hook. When that process ends, the runner leaves the queue and
  closes its job. A runner never starts its command for a session that is
  gone.
- In the app the session is also inside `ChatSession`'s job, which ends what
  a finished turn left.

### The owner's real data folder

In a oneshot build session (`WIKI_AGENT_MANAGED`, a run whose stage is a
build stage) the program's real per-user folders are refused: `%APPDATA%` and
`%LOCALAPPDATA%` joined with the repository's name, and with the
`identifier` and `productName` of each `tauri.conf.json` in the worktree.

- A shell call whose text names one, in any spelling (`%LOCALAPPDATA%\x`,
  `$LOCALAPPDATA/x`, the absolute path, either slash, any case), is denied.
- An edit or a write inside one is denied.
- A program can still write there by its own default with no path in the
  command. At a build turn's start the server records, for each of those
  folders, every file's path, size and modification time. At the turn's end
  (`oneshot_build.checked`, beside `oneshot_needs.leaked`) it compares
  them. A file created, changed or removed refuses the turn, naming the
  folder and up to five files, and the build gets it back as its next note.
- The build is told the folder its own runs use instead:
  `measure-app\agent` beside the server's `measure-app\data`. Its first run
  there downloads the program's runtime once.

### What the build is told

The build prompt gains a short section: the machine line, that GPU jobs
queue and that its own GPU holder must be stopped before the next, that the
owner's real folders are refused and checked at the turn's end, and which
folder to use. The checks above are what holds; the section only explains
the refusals before they happen.

### Done when

- Tests show each refusal and each pass above, the queue's order, a lease
  freed by killing its runner, a runner leaving when its session ends, and a
  turn refused for a file left in the owner's folder.
- The full gate passes once after the reviewer allows the merge.

## 15b — the build's loop measures on the server, watched when wanted

### The build no longer measures

In a oneshot build session a shell call is denied when it sets or names
`ONESHOT_MEASURE`, or runs the measurement script the contract names. A
build turn that wants a number ends; the server measures the real program,
as it does after every build turn, and the result comes back as the next
turn's note. That is the loop: measure, fix, measure again, for each
scenario.

### The measurement keeps what a diagnosis needs

What the program writes to its output and its log files during a
measurement is kept, masked, in the measurement's folder, and the next
build note names those files. A build that needs more detail adds logging
to the program and commits it; the next measurement records it. No build
turn needs a run of its own to see inside the program.

### On a screen of its own, watched when wanted

- The server's automatic measurements start the program on a desktop of
  their own again (`desktop_session`, the part #446 removed, restored from
  history), so nothing appears on the owner's screen.
- The program's sound is muted on its own audio session, so the owner hears
  nothing either. The measurement's sound checks read the program's own
  output, as today.
- The running measurement card gains [보기]. It shows the program's window
  live, a frame every half second, and plays the program's output with its
  sound. Closing it stops the stream; the measurement goes on.
- The clips (`live.mp4`, `video.mp4`) stay on the card after the run.
- The owner's own round, and every metric marked `person`, still open on
  the owner's screen with sound, as today.
- First, before any of this is built: check on this PC that WebView2 draws
  on a desktop of its own and that muting the audio session leaves the
  output's sound intact. If either fails, stop and ask the owner.

### Pages that change

The rule that every measurement shows on the owner's screen is rewritten in
place to say the above, wherever it is written.

### Done when

- Tests show the build's measurement refused, the logs handed to the next
  note, the program on its own desktop and muted, and [보기] showing the
  live window.
- The full gate passes once after the reviewer allows the merge.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 15a | The machine line, the load line, the own-holder refusal, the GPU queue with its runner, the owner's data folder refused and checked at the turn's end, the prompt section | Not started |
| 15b | The build's measurement refused, the logs kept for the next note, the measurement on its own desktop and muted, [보기] with live window and sound, the pages rewritten | Not started |
