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
| Orphans from queueing | Not left by the queue: Windows ends what a queue runner started when the runner ends, a place in the queue is held only by a live process, and a runner ends with the shell and the session that started it |
| Waits | No wait or timeout is shortened anywhere ([moved commands](../../../operator/decide-moved-background-commands.md)) |
| Who measures | Only the server, on the real program. The build asks the server for a measurement and never runs the measurement script itself |
| Where the owner sees it | The build's repeated measurements run on a screen of their own; the owner watches one live from the measurement card when they want, and the clips stay. What the owner must check by hand still opens on the owner's screen |
| How it ships | Two pull requests, 15a and 15b, in that order. Neither is split further, and nothing outside its task list goes into it |

## 15a — the machine's size, its load, the GPU queue, the owner's data

This applies to every Claude and Codex session wired to this hub, not only
oneshot runs, except where a line names a build session. A build session is
the CLI the server starts for a oneshot build cell. The server marks it with
`WIKI_AGENT_ROLE=build` when it starts it. A check that names a build session
also reads the run's effective turn stage (`oneshot_profile.turn_stage`), so
a repair sent from `measure` counts as a build turn.

### The machine, once a session starts

`tool/machine.py` reads the CPU model with its cores and threads, the RAM in
GB, each GPU's model and VRAM, and each disk's model, type (SSD, HDD) and
bus, from WMI and `nvidia-smi`. It reads them once per boot and keeps them
under the temporary folder. `session_state` adds one line with them to the
session's opening context. A value it cannot read is left out, never guessed.

### The load, at each program start

The same file reads the load now:

- CPU percent, from two `GetSystemTimes` reads 100 ms apart;
- RAM used and total, from `GlobalMemoryStatusEx`;
- VRAM used and total, and the processes the GPU lists, from `nvidia-smi
  --query-gpu=memory.used,memory.total` and `--query-compute-apps=pid,process_name`.

A sample takes about 0.2 s and is kept for 2 s, inside the pretool deadline
(`hook_deadline`). A part that fails or runs out of time shows as `읽지
못함`; a sample older than 10 s is never shown.

What Windows shows, pinned on this PC on 2026-11-26 (RTX 3080, WDDM): the GPU
lists every process using it, graphics users such as `explorer.exe` and
WebView2 among them, each with memory `N/A`. So the line names the listed
processes and the total, never a process's share, and a listed pid with
memory `N/A` is still a holder.

A shell call gets the line when it starts a program. A call whose program
words are all inspection gets nothing: `git`, `grep`, `rg`, `ls`, `cat`,
`head`, `tail`, `sed -n`, `find`, `wc`, `nvidia-smi`, `tasklist`, `netstat`,
`Get-Process`, `Get-Content`, `Select-String`. Program words are read as
`guard` reads them, nested shells included.

### Which commands are GPU jobs

Exactly two kinds of program word:

1. An `.exe` that resolves inside the session's repository, its worktrees or
   the temporary folder: a build of the product and its copies.
2. A Python interpreter whose environment holds a CUDA build: a `torch` whose
   `dist-info` version carries `+cu`, an `nvidia` package folder, or
   `onnxruntime-gpu`. It covers `python`, `python3` and `py` as the call's
   `PATH` resolves them or by explicit path, and `uv run` in a project whose
   `.venv` holds one, or whose `--with` names one.

On this PC the default `python` (Anaconda) holds a CPU `torch` (2.13.0)
and is no GPU job, so the hub's own tools and `pytest` are not; the product
runtime's `torch 2.11.0+cu128` is. A GPU job started any other way, through
a task runner or a script that launches one, is not seen. The free-memory
floor below is what still guards it.

### The session's own processes

- In the app, `ChatSession` creates its job under a name,
  `Local\wiki-agent-session-<uuid>`, and passes the name to the CLI as
  `WIKI_AGENT_JOB`. The hook opens that job and checks with `IsProcessInJob`
  that it is a member itself; then the job's process list is the session's
  own, a process whose parent has exited included. A launcher's or sandbox's
  job the hook also runs in is never read.
- Elsewhere they are the descendants of the nearest ancestor that is the
  Claude or Codex program. A process whose parent has exited drops out of
  that tree, so outside the app such an orphan is not counted as the
  session's own; the floor still applies to it.
- When neither proves anything, the session counts as owning no process,
  and the load line says `세션 소유 확인 불가`.

### Refused behind its own holder

A GPU job is refused, on Claude and on Codex, while one of the session's own
processes is on the GPU's list. The reason names the pid, the program and
its command line. It says to stop it, wait for it, or start a server and
what uses it in one command so they end together. A queue behind one's own
idle server would never move, so this case never queues. On 2026-11-26 at
10:03 it refuses `sim_why.py`, run by the product runtime's Python, while
`vtuber-serve.exe` (pid 3032) is on the list.

### The queue, on Claude

1. Every check judges the call as the agent wrote it: `deny`, `guard` and
   the rest run first, on the repaired command and its working directory,
   as today. Only a GPU job they all let through is wrapped, last, in
   `claude_pretool`: the command becomes `tool/gpu_run.py` around the
   original, and the call gets `run_in_background: true`. The context says
   it queued, that its output names its place, and that a notice comes when
   it ends.
2. Every GPU job runs in the background, so no foreground timeout counts a
   wait, and the hook never names a place it cannot reserve. Claude Code
   2.1.332 stops a command its timeout moved after 30 minutes in the
   background, and does not stop one started there: on 2026-11-26 a
   `run_in_background` backend ran 62 minutes until the agent ended it.
3. `background_left` holds a wrapped call as it holds a moved one. The
   agent asked for it in the foreground and the hook moved it, so the
   session waits for it, reads it or stops it before other tools. The
   moved-command page gains this case.

On Codex the shell tool has no background start, and a rewrite there comes
only with `allow`. So Codex is never queued: a GPU job that would wait is
refused, naming the head of the queue and how many places wait.

### The queue's order

One protocol serves the runners and the server alike.

- A place is a file `%TEMP%\wiki-agent-gpu\queue\<ns>-<pid>-<random>`,
  created with `CREATE_NEW` and no sharing, and held open by its owner while
  it waits and runs. `<ns>` is `time.time_ns()` at creation, zero-padded.
  Places are served in name order.
- The head is the first place whose file cannot be opened because of a
  sharing violation, so a live process holds it. A place whose file opens
  belongs to no one; see the next section before it is removed. Any other
  error leaves the place counted as live, and the waiting line names its
  file. A place is never removed on a guess.
- Only the head runs, and holding the head place while running is the
  lease. The head first waits until free VRAM is at least 20 % of the total
  and free RAM at least 15 %, saying so in its output. This covers what uses
  the GPU outside the queue: a game, another program, a session this hub
  does not reach. The head waiting on the floor holds every later place.
  Nothing overtakes, so nothing starves.
- The server's own launches take places by the same protocol and keep them
  until the program has ended: the automatic PC measurement, the build
  gate's health check (`serve_check`), the trial measurement (15b) and the
  owner's round. Their wait comes before their launch and script deadlines
  start. A stop (`halt`) while waiting drops the place and starts nothing.
  The measurement card shows `GPU 대기 중 — 앞에 N개`.

### The runner leaves nothing behind

- The runner holds handles to the shell that started it and to the
  session's Claude process, whose pid and start time the hook passes, so a
  reused pid is not taken for it. Claude's TaskStop ends that shell and
  leaves its child running (`chat_session.py`, seen on 2.1.291), so the
  runner watches the shell. When either ends, it drops its place and ends.
- It starts the command suspended and puts it in a job of its own that ends
  every member when it closes (`common.process.contained`). Only when that
  succeeded does it resume it. A failed containment starts nothing and says
  why.
- Right before it starts the command, and again once it is contained, it
  checks both watched processes. If either has ended, it ends the job before
  anything runs.
- It writes the pid and start time of each member of its job into its own
  place file as they appear. However the runner ends, Windows closes its
  handles, which ends the job's members and frees the place. A new head that
  finds a free place reads those members and waits until each has exited
  before it removes the place and starts. So no next command starts beside
  what the last one left.
- In the app the session is also inside `ChatSession`'s job, which ends
  what a finished turn left.

### The owner's real data folder, in a build session

The roots are, for each of `FOLDERID_RoamingAppData` and
`FOLDERID_LocalAppData`, read with `SHGetKnownFolderPath`, and the
`APPDATA` and `LOCALAPPDATA` values where they differ: that folder joined
with the repository name, and with the `identifier` and the `productName` of
each `tauri.conf.json` in the worktree. A product that keeps its data
elsewhere is not covered until its kind names the place.

1. Text. A shell call whose text names a root, in any spelling
   (`%LOCALAPPDATA%\x`, `$LOCALAPPDATA/x`, `$env:LOCALAPPDATA\x`, the
   absolute path, either slash, any case), is refused. So is an edit or a
   write inside one. A call that names only the variable is not.
2. Launch. The runner starts a product `.exe` (the first kind of GPU job)
   with `APPDATA` and `LOCALAPPDATA` pointing into the run's agent folder. A
   program that reads those variables lands there. One that asks Windows
   for the known folder does not, and the watch below sees it.
3. Watch. When a build session's turn starts, the server watches each root
   that exists with `ReadDirectoryChangesW` over its subtree (names, sizes,
   writes), and the parent of each root that does not, for its creation.
   Nothing is walked: the vtuber root holds 200,024 files now, and one
   metadata walk of it took 20 s. A buffer overflow counts as a change with
   no names. Junctions inside a root are not followed.
4. End. However the turn ends (answered, failed or stopped),
   `work.run_turn`'s finalization, beside `oneshot_needs.settle`, stops the
   watch. What changed is recorded in the run as `owner_data`: the turn and
   up to five paths. While that record stands, `oneshot_build.checked`
   refuses the build's pass, and the record is the next build turn's note.
   It clears when a build turn ends with no change.
5. Whose change. A change during a build turn is taken as the build's. The
   product is under construction and its installer is this run's own output,
   so no other writer is expected. If the owner runs a copy of it during a
   turn, the refusal names the files and the owner resumes the run.
6. The build's folder. The run's `measure-app\agent`, beside the server's
   `measure-app\data`, is added to the build session's write roots
   (`guard_grant`). The first launch there downloads the runtime once.

### What the build is told

The build prompt gains a short section: the machine line, that GPU jobs
queue and that its own GPU holder must end first, that the owner's real
folders are refused and watched, and which folder its launches use. The
checks above are what holds; the section only explains the refusals before
they happen.

### Done when

Tests show:

- the machine line and the load line, with a part unreadable;
- both GPU-job kinds and a CPU `torch` that is no GPU job;
- the own-holder refusal in the app's named job and by descent, with an
  exited parent and with an unrelated job the hook also runs in;
- the wrapped call judged before wrapping (an out-of-grant write after a
  product `.exe` still refused), its background start and its hold;
- the queue's order, a stale place, an unreadable place, the floor holding
  the head, and the server's launches waiting and stopping in it;
- the runner ending its job when its shell, its session or itself ends,
  a failed containment starting nothing, and a new head waiting for the last
  job's members;
- Codex refused instead of queued;
- each spelling of a root refused, the launch's variables, a change seen by
  the watch on an answered, a failed and a stopped turn, and the pass held
  until a clean turn.

The full gate passes once after the reviewer allows the merge.

## 15b — the build's loop measures on the server, watched when wanted

### First, the check that gates 15b

Before anything below is built, with the owner's go-ahead for that step, on
this PC, with the run's actual debug product started on a desktop of its own:

1. `PrintWindow` gets 60 s of frames of its window while a video plays, at
   least 100 of them different.
2. With the program's audio sessions muted, a loopback capture of the
   default speaker hears nothing from it, while a process loopback capture
   of the program's processes hears its sound.
3. Stopping both captures leaves the program and its measurement running.

If any of the three fails, 15b stops there and goes to the owner.

### The build asks the server to measure

A build turn may end with a `measure-request` block, a fenced block as
`done-report` is, naming the contract's metric ids to run, or `all`. The
server answers it with a trial measurement:

- of the worktree's `HEAD`: an uncommitted tree refuses the request, naming
  the files, and the build commits what it wants measured, diagnostic
  logging included;
- the real program from a frozen copy, the contract's script, the run's
  measurement folder, a GPU place taken as for any measurement;
- no build gate first: a trial is the build's diagnosis and never passes or
  fails the run;
- its result and its logs come back as the next build turn, at the same
  stage, and the card lists it as `시험 실측 N`.

The run's own measurement does not change. It runs after the build stage's
checks pass and its `HEAD` is published (`oneshot_publish`). So the loop is:
build, ask, fix, ask again, for each scenario, then the gates, then the run's
measurement.

### The build's own measurement is refused

In a build session, on Claude and on Codex, a shell call is refused when its
text sets `ONESHOT_MEASURE` (`ONESHOT_MEASURE=`, `$env:ONESHOT_MEASURE =`,
`set ONESHOT_MEASURE=`, `export ONESHOT_MEASURE=`) or runs the contract's
measurement command as the contract writes it. Reading the contract or the
script passes, and so does a test that sets the variable in its own code.

A command that reaches the script through another runner is not seen. The
request above is the faster path and the one the prompt names; the refusal
catches the direct call, the one the 2026-11-26 turn made four times.

### The logs, for the next note

- Kept: the program's output, already captured and masked, and under the
  measurement's data folder each file created or changed during this
  measurement whose name ends `.log`, `.txt` or `.jsonl`, newest first, up to
  5 MB in all, masked as the output is. Earlier measurements' files are left
  out by their times.
- Copied to `%TEMP%\wiki-agent-oneshot\<repo>\m<n>\`, inside the build's read
  roots, and named in the next build note. The server's folder is not opened
  to the build.

### On a desktop of its own, muted, watched when wanted

- `desktop_session.look` gains `own=True`, a desktop of its own, the part
  #446 removed, restored from its history. Only three launches pass it: the
  automatic PC measurement, the trial measurement and `serve_check`. The
  owner's round, every metric marked `person`, and every other look stay on
  the owner's desktop. #446's input questions and its repair at the
  measurement do not change.
- On start the server mutes the program's audio sessions (`ISimpleAudioVolume`
  for each of its processes). What the viewer hears is a process loopback
  capture of those processes.
- The running measurement card gains [보기], served by the app's own server
  as the rest of the card is. It is bound to one running measurement: frames
  of that measurement's own windows only, every 0.5 s by `PrintWindow` on
  its desktop, and the capture of its own processes' sound. Nothing of it is
  stored. The stream closes when the page leaves, when the measurement ends
  or stops, and when another measurement replaces it. A secret typed into
  the product can show in a frame; frames go only to the owner's own app
  page and are kept nowhere.
- The clips (`live.mp4`, `video.mp4`) stay on the card after the run.

### Pages that change

Rewritten in place to say the above: plan 13's passages on where the program
appears (13f), `tool/prompts/oneshot-measure-contract.md`,
`oneshot-build-front-desktop.md`, `oneshot-frontend-desktop.md`,
`oneshot-review-desktop.md`, and the conclusion of
`docs/research/oneshot-desktop-session.md`.

### Done when

Tests show:

- a `measure-request` measured as a trial, refused on an uncommitted tree,
  its result back as the next build turn, and the run's measurement
  unchanged;
- each spelling of the variable and the contract's command refused, and a
  read of the contract passing;
- the logs chosen by time and size, masked, copied, and named in the note;
- only the three launches on their own desktop, muted;
- [보기] bound to its measurement and closed on each of its three ends.

The full gate passes once after the reviewer allows the merge.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 15a | The machine line, the load line, the two GPU-job kinds, the session's own processes and the refusal behind them, the Claude queue and the Codex refusal, the queue's order shared with the server's launches, the runner, the owner's data folder refused, redirected for launches and watched over every turn, the prompt section | Not started |
| 15b | The check that gates it, `measure-request` and the trial measurement, the build's own measurement refused, the logs for the next note, three launches on their own desktop and muted, [보기], the pages rewritten | Not started |
