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
| A heavy command that cannot run now | It waits in a queue rather than failing. Codex cannot start a call in the background, so there it runs at once or is refused; this part is for the owner to confirm with the plan |
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

What Windows shows, pinned on this PC on 2026-11-26 (RTX 3080, WDDM):
`nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv`
listed `explorer.exe`, `ShellHost.exe`, `msedgewebview2.exe` and others, each
with memory `[N/A]`, and some names as `[Insufficient Permissions]`. So on
WDDM that query lists graphics users as well, the line names the listed
processes and the total, never a process's share, and a listed pid with
memory `N/A` or no name is still a holder. With several NVIDIA adapters,
every adapter is read and the floor below must hold on each.

When a part cannot be read:

- No `nvidia-smi`, or its query fails: nothing about the GPU is known. The
  own-holder refusal cannot apply and the call passes it; the VRAM floor is
  not applied; the RAM floor still is. The line says the GPU was not read.
- RAM unreadable: the RAM floor is not applied, and the line says so.
- The queue's lease (below) never depends on a sample: exclusive running is
  held by the lease alone.

A shell call gets the line when it starts a program. A call whose program
words are all inspection gets nothing: `git`, `grep`, `rg`, `ls`, `cat`,
`head`, `tail`, `sed -n`, `find`, `wc`, `nvidia-smi`, `tasklist`, `netstat`,
and PowerShell's own reads of processes and files. Program words are read as
`guard` reads them, nested shells included.

### Which commands are GPU jobs

Exactly two kinds of program word, read in this order:

1. A Python interpreter is a GPU job exactly when its environment holds a
   CUDA build: a `torch` whose `dist-info` version carries `+cu`, an
   `nvidia` package folder, or `onnxruntime-gpu`. This covers `python`,
   `python3`, `py` and any `python.exe` or `pythonw.exe`, as the call's
   `PATH` resolves them or by explicit path, a worktree's
   `.venv\Scripts\python.exe` included, and `uv run` in a project whose
   `.venv` holds one, or whose `--with` names one. An interpreter is decided
   by this rule only; the next rule never applies to it.
2. Any other `.exe` that resolves inside the session's repository, its
   worktrees or the temporary folder: a build of the product and its copies.
   A CPU-only program there, a test fixture for one, counts too. It then
   runs in the queue and in the background, which costs it a wait and
   nothing else.

On this PC the default `python` (Anaconda) holds a CPU `torch` (2.13.0)
and is no GPU job, so the hub's own tools and `pytest` are not; the product
runtime's `torch 2.11.0+cu128` is. A GPU job started any other way, through
a task runner or a script that launches one, is not seen. The free-memory
floor below is what still guards it. Tests cover the Anaconda case and a
CPU and a CUDA Python inside a worktree, by `PATH` and by explicit path.

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
3. `background_left` holds a wrapped call as it holds a moved one: the
   session waits for it, reads it or stops it before other tools. A call
   the agent itself started with `run_in_background` stays exempt. The hold
   finds a wrapped call by the runner's marker, `gpu_run.py --hold`,
   in the command the transcript records for the call, or else by a call
   recorded without `run_in_background` whose result starts a background
   task. Which of the two Claude Code 2.1.332 records is checked against a
   real transcript before 15a is built, and both are tested. A call matched
   by neither is not held, the same as today. The moved-command page gains
   this case.

On Codex the shell tool has no background start, and a rewrite there comes
only with `allow`, after which the approval policy still decides. So a
Codex GPU job is wrapped in the runner with `--now`: the runner takes the
lease only if no place waits and the lease is free that moment, and holds it
for the command's whole run in the foreground. Otherwise it runs nothing and
exits, naming the head and how many places wait. A Codex GPU job never
waits, so no Codex timeout counts a wait.

### The queue's order and the lease

One protocol serves the runners and the server alike, in
`tool/gpu_queue.py`.

- The lease is a named mutex, `Local\wiki-agent-gpu`, which every process of
  the owner's logon session shares. Holding it is the only permission to
  run a GPU job, so two jobs never run at once, whatever the queue's state.
  It is owned by the thread that runs the job: a runner's main thread, or
  the server's measurement worker thread, which takes and releases it
  itself. When its owner ends without releasing it, Windows hands it to the
  next waiter as abandoned (`WAIT_ABANDONED`).
- A place gives the order. A second mutex, `Local\wiki-agent-gpu-queue`,
  guards taking one: under it the taker reads and raises a counter in
  `%TEMP%\wiki-agent-gpu\next`, and creates the place file
  `%TEMP%\wiki-agent-gpu\queue\<counter>` with `CREATE_NEW` and no sharing,
  held open while it waits and runs. The counter, not the clock, orders
  places, so a paused taker or a clock change cannot put a place ahead of
  one taken before it.
- The head is the lowest place whose file cannot be opened because of a
  sharing violation, so a live process holds it. A place whose file opens
  belongs to no one and is removed under the queue mutex. Any other error
  leaves the place counted as live, and every waiter's output names its
  file. A place is never removed on a guess. Such a place stops the queue
  until it can be decided. The waiting line says so and names the file; the
  owner deletes it once its process is gone, and nothing is timed out.
- Only the head waits on the lease. Once it holds the lease, it waits until
  free VRAM is at least 20 % of the total and free RAM at least 15 %, saying
  so in its output, then runs. The floor covers what uses the GPU outside
  the queue: a game, another program, a session this hub does not reach. The
  head waiting on the floor holds every later place, so nothing overtakes
  it. A head that waits on a floor nothing frees waits as long as that lasts;
  the line names what holds the memory.
- The server's own launches take a place and the lease by the same protocol
  and keep them until the program has ended: the automatic PC measurement,
  the build gate's health check (`serve_check`) and the trial measurement
  (15b). The owner's round takes the lease and a place too but skips the
  floor: the owner is there and decides. Their wait comes before their
  launch and script deadlines start. The measurement card shows `GPU 대기 중
  — 앞에 N개`.

### The runner leaves nothing behind

- The runner holds handles to the shell that started it and to the
  session's Claude or Codex process, whose pid and start time the hook
  passes, so a reused pid is not taken for it. Claude's TaskStop ends that
  shell and leaves its child running (`chat_session.py`, seen on 2.1.291),
  so the runner watches the shell. When either ends, it drops its place and
  the lease and ends.
- It creates the command suspended and puts it in a job of its own that
  ends every member when it closes (`common.process.contained`). A failed
  containment starts nothing and says why.
- Before it resumes the command it writes the command's pid and start time
  into a lease note, `%TEMP%\wiki-agent-gpu\holder`, then checks both
  watched processes. Only if both live does it resume. One that ends after
  that check is seen by the runner's wait on both handles at once, and the
  runner ends the job then; between that check and the end of the job the
  command can run for as long as Windows takes to report the exit. On a
  server launch, an accepted stop and the resume are serialized by the
  existing launch gate (`desktop_session`), so an accepted stop either comes
  before the resume and nothing runs, or after it and ends the job.
- However the runner ends, Windows closes its handles: the job's members are
  ended and the lease is abandoned. A holder that receives an abandoned
  lease reads the lease note and waits until that process has exited, then
  waits for the floor, before it runs. ponytail: a descendant of that
  process is ended by the same job close but is not waited on one by one; the
  floor is what keeps the next job off memory it still holds.
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
   absolute path, either slash, any case), is refused, on Claude and on
   Codex. So is an edit or a write inside one. A call that names only the
   variable is not.
2. Where the build's launches keep data. The run's `measure-app\agent`,
   beside the server's `measure-app\data`, is added to the build session's
   write roots (`guard_grant`). The build passes it the way the contract's
   `app.run` passes `{data}`, an argument or a variable, whichever the
   contract declares. Nothing redirects a launch by itself; what holds is
   the refusal above and the watch below. The first launch there downloads
   the runtime once.
3. The watch. Before a build session's turn is sent, the server writes a
   hold in the run, `owner_watch: {turn, started}`, as `oneshot_needs.owe`
   writes its hold, and starts watching. For each root that exists it
   watches the root's subtree with `ReadDirectoryChangesW` (names, sizes,
   writes), and the root's parent, not recursively, for the root itself
   being renamed, removed or replaced. For a root that does not exist, it
   watches the parent for its creation. Nothing is walked: the vtuber root
   holds 200,024 files now, and one metadata walk of it took 20 s. Junctions
   inside a root are not followed.
4. What the watch can say. `clean`; `changed` with up to five paths, or with
   none when the change is a buffer overflow or the root itself changed;
   `incomplete` when a watch could not open, a read failed, or the server
   stopped before the turn's end, which is what a hold left over from an
   earlier server means.
5. When it counts.
   - `oneshot_build.checked` reads the watch first, before any check runs
     and before any check can save the run's next stage. So the window it
     judges is the build session's own: from the send until its answer.
     `changed` or `incomplete` refuses the turn with the folder and the
     paths, and that is the next build turn's note.
   - A turn that ends without reaching `checked`, failed or stopped, is
     read at `work.run_turn`'s finalization, beside `oneshot_needs.settle`.
     Its result goes into the next build turn's note.
   - A refused or noted turn does not hold the next one: each turn is judged
     on its own window, and the first clean turn passes.
   - A hold found from a server that stopped mid-turn stops the run for the
     owner with `주인 데이터 폴더를 감시하지 못한 구간이 있다 — 확인 후
     재개`. An unwatched window is never taken for a clean one; the owner's
     resume clears it.
   - The server's own launches of the product (`serve_check`, the PC
     measurement, the trial) watch the same roots over their run. A change
     fails that launch with the folder and the paths, as a failed metric
     does.
6. Whose change. A change in a build session's window is taken as the
   build's. The product is under construction and its installer is this
   run's own output, so no other writer is expected. A change shows only
   which paths changed, never which process wrote them. If the owner runs a
   copy of it during a turn, the refusal names the files and the owner
   resumes the run.

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
  product `.exe` still refused), its background start and its hold, found
  by either record, beside an agent's own background call left exempt;
- the lease exclusive under racing takers, places ordered by the counter
  with a taker paused before it creates its file, a stale place, an
  unreadable place stopping the queue, the floor holding the head, an
  abandoned lease waiting for the noted process, and the server's launches
  waiting and stopping in it;
- the runner ending its job when its shell, its session or itself ends, a
  shell ending between the last check and the resume, a failed containment
  starting nothing, and a server stop on either side of the resume;
- Codex admitted with `--now` holding the lease for its run, and refused
  when a place waits;
- the GPU unreadable, RAM unreadable, and two adapters;
- each spelling of a root refused on both hosts, the watch's three results
  on an answered, a failed and a stopped turn, a root replaced during a
  turn, the turn refused before any stage is saved, the first clean retry
  passing, a leftover hold stopping the run for the owner, and a server
  launch failed by a change.

The full gate passes once after the reviewer allows the merge.

## 15b — the build's loop measures on the server, watched when wanted

### First, the check that gates 15b

Before anything below is built, with the owner's go-ahead for that step, on
this PC, with the run's actual debug product started on a desktop of its own:

1. `PrintWindow` gets 60 s of frames of its window while a video plays, at
   least 100 of them different.
2. Muting as the measurement will mute (below), with playback started after
   launch the way the measurement script starts it: a loopback capture of
   the default speaker hears nothing from the program from its first sample
   on, while a process loopback capture of the program's processes hears
   its sound.
3. Stopping both captures leaves the program and its measurement running.

If any of the three fails, 15b stops there and goes to the owner.

### The build asks the server to measure

A build turn may end with a `measure-request` block, a fenced block as
`done-report` is. It names nothing: a trial runs the contract's script
whole, in `auto` mode, as the automatic measurement does, so its `person`
metrics stay unmeasured and no metric is selected or skipped. The script's
outside calls happen as in any automatic measurement.

The server answers with a trial measurement:

- It measures the worktree's `HEAD`. An uncommitted tree refuses the
  request, naming the files; the build commits what it wants measured,
  diagnostic logging included.
- It builds that `HEAD` with the contract's build for the program (the
  `app.run` build the measurement already requires) before freezing it, so
  output left from an earlier commit is never measured under a new one. The
  frozen copy goes to its own folder, `measure-app\trial\app`, and never
  replaces `measure-app\app`, which the owner's round may be waiting to run.
- It takes a GPU place and the lease as any measurement does.
- No build gate runs first. A trial is the build's diagnosis and never
  passes or fails the run.

Its state is its own: the run's `trials` list holds each trial's number,
`HEAD`, build identity, start, end, result and kept logs, apart from
`measures`. It is dispatched once the build turn has ended and released the
worktree, as the measurement is after publication. The measurement view
shows a `시험 실측` section at every build stage, `built_head` or not, with
the running trial, its [보기] and [중지], and the finished trials. When it
ends, its result and its logs go back as the next build turn, at the same
stage:

- an input the script calls wrong goes into that note as it is, with no
  question to the owner, since a trial decides nothing;
- [중지] or the run's own stop ends it; the run stays stopped, and on resume
  the next build turn is told the trial was stopped;
- a server that stops mid-trial leaves it `interrupted` on restart, and the
  next build turn is told so.

The run's own measurement does not change. It runs after the build stage's
checks pass and its `HEAD` is published (`oneshot_publish`). So the loop is:
build, ask, fix, ask again, for each scenario, then the gates, then the run's
measurement.

### The build's own measurement is refused

In a build session, on Claude and on Codex, a shell call is refused when its
text sets `ONESHOT_MEASURE` (a `VAR=` prefix, `set`, `export`, or a
PowerShell `$env:` assignment, in any case), or when it runs the contract's
measurement command: the command's program words matched with its one
`{out}` taken as any single argument, so the call the server itself makes,
with a real folder in its place, matches. Reading the contract or the
script passes, and so does a test that sets the variable in its own code.

A command that reaches the script through another runner is not seen. The
request above is the faster path and the one the prompt names; the refusal
catches the direct call, the one the 2026-11-26 turn made four times. Tests
use that exact call shape.

### The logs, for the next note

- What is kept: the program's output, already captured and masked; and,
  under the measurement's data folder, files whose names end `.log`, `.txt`
  or `.jsonl`. For a file that existed when the measurement started, only
  the bytes written after its size at that start are kept, so an append
  log brings no earlier run's lines. A file created during the measurement
  is kept whole. Newest first, up to 5 MB in all, masked as the output is.
- Each path is checked as `oneshot_kept` checks evidence today: a reparse
  point or a file with more than one link is refused, and a name is checked
  before it is shown.
- The copies go to `%TEMP%\wiki-agent-oneshot\<repo>\<trial or
  measurement>-<n>\`, inside the build's read roots, and the next build note
  names them. They are removed when the build turn after that note ends. The
  server's folder is not opened to the build.

### On a desktop of its own, muted, watched when wanted

- `desktop_session.look` gains `own=True`, a desktop of its own, the part
  #446 removed, restored from its history. Only three launches pass it: the
  automatic PC measurement, the trial measurement and `serve_check`. The
  owner's round, every metric marked `person`, and every other look stay on
  the owner's desktop. #446's input questions and its repair at the
  measurement do not change.
- Muting covers every audio session of the program's job, from before the
  program runs to its end. The server registers for new sessions on the
  default output device (`IAudioSessionManager2`, `IAudioSessionNotification`)
  before it resumes the program, and mutes each session of a job member as
  it is created, child processes included. A session it cannot mute, or a
  mute it cannot read back as set, fails the launch with that reason; the
  program does not go on playing.
- What the viewer hears is a process loopback capture of the job's
  processes.
- The running measurement or trial card gains [보기], served by the app's
  own server and refused to any request through the paired mobile tunnel
  (`mobile.local`). It names the run and the measurement's id; the server
  refuses an id that is not the one running now. It is bound to that one
  measurement: frames of its own windows only, every 0.5 s by `PrintWindow`
  on its desktop, and the capture of its own processes' sound. Nothing of it
  is stored. The stream closes when the page leaves, when the measurement
  ends or stops, and when another measurement replaces it. A secret typed
  into the product can show in a frame; frames go only to a local page of
  the app and are kept nowhere.
- The clips (`live.mp4`, `video.mp4`) stay on the card after the run.

### Pages that change

Rewritten in place to say the above: plan 13's passages on where the program
appears (13f), `tool/prompts/oneshot-measure-contract.md`,
`oneshot-build-front-desktop.md`, `oneshot-frontend-desktop.md`,
`oneshot-review-desktop.md`, and the conclusion of
`docs/research/oneshot-desktop-session.md`.

### Done when

Tests show:

- a `measure-request` measured as a trial of a freshly built `HEAD` in its
  own copy, refused on an uncommitted tree, its card before `built_head`,
  its result back as the next build turn after an end, a wrong input, a
  stop and a restart, and the run's measurement and the owner's copy
  unchanged;
- each spelling of the variable and the contract's command with a real
  folder for `{out}` refused, and a read of the contract passing;
- the logs cut at each file's starting size, refused for a reparse point or
  a second link, masked, copied, named in the note and removed after;
- only the three launches on their own desktop; a session created after
  launch and a child's session muted; a mute that fails ending the launch;
- [보기] refused through the tunnel and for a measurement not running,
  bound to its measurement, and closed on each of its three ends.

The full gate passes once after the reviewer allows the merge.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 15a | The machine line, the load line, the two GPU-job kinds, the session's own processes and the refusal behind them, the Claude queue and the Codex run-now-or-refuse, the lease and the queue's order shared with the server's launches, the runner, the owner's data folder refused and watched over every build turn and server launch, the prompt section | Not started |
| 15b | The check that gates it, `measure-request` and the trial measurement with its own state and card, the build's own measurement refused, the logs for the next note, three launches on their own desktop and muted, [보기], the pages rewritten | Not started |
