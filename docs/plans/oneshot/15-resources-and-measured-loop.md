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
`WIKI_AGENT_ROLE=build` when it starts it. The worktree's CLI is cached and
shared with the owner's own turns (`work.session`), so the role is part of
what makes a cached CLI reusable: the build's send passes the role to
`work.session`, which puts it in the session's environment, and the owner's
send passes none. `work.session` already closes a cached CLI whose
environment differs and starts the next with `--resume`, so a build turn
never runs in an unmarked CLI and an owner's turn never runs in a marked
one, whatever the stage. A check that names a build session also reads the
run's effective turn stage (`oneshot_profile.turn_stage`), so a repair sent
from `measure` counts as a build turn.

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
Codex GPU job is wrapped in the runner with `--now`, and the runner decides
in one step under the queue mutex (below). First, no live place may wait.
Then it tries the lease without waiting. Only once it owns the lease does it
read the holder note, and judges the whole note as any holder does (below):
a note that names any process still alive or proven nothing, or a floor
that does not hold, makes it release the lease again; a note whose every
named process has exited it clears, as the lease's owner. Then it holds the lease for the command's whole
run in the foreground. A place published after that step waits on the lease
like any other. If any condition fails it runs nothing and exits, naming
which: the head and how many places wait, the lease held, the leftover
holder, or the memory short. A Codex GPU job never waits, so no Codex
timeout counts a wait.

### The queue's order and the lease

One protocol serves the runners and the server alike, in
`tool/gpu_queue.py`.

- Whose it is. The queue belongs to the account that owns the hub
  checkout's folder (its owner SID, read with `GetNamedSecurityInfo`).
  Before it resolves or creates anything, a participant compares its own
  token's user SID with that one. A process of another account, such as the
  Codex sandbox's separate Windows account, or one that cannot read the
  owner, runs nothing and says that this account cannot join the GPU queue.
  So no other account ever creates the mutexes or a folder of its own.
- Where it lives. Every participant uses one folder, `wiki-agent-data\gpu`
  under the owner's `FOLDERID_LocalAppData`, read with
  `SHGetKnownFolderPath`, never from `TEMP` or `LOCALAPPDATA`. So the
  server's gate, which points `TEMP` elsewhere, and every shell see the same
  queue. The two mutexes below are created in the `Local\` namespace of the
  owner's logon session with the owner account's default security.
- The lease is a named mutex, `Local\wiki-agent-gpu`. Holding it is the
  only permission to run a GPU job, so two jobs never run at once, whatever
  the queue's state. It is owned by the thread that runs the job: a runner's
  main thread, or the server's measurement worker thread, which takes and
  releases it itself.
- The holder note, `gpu\holder`, is what survives a holder that ends
  without cleaning up. Every holder, runner or server, writes it right after
  it takes the lease and before its job's first process runs: its own pid
  and start time, and the job's root process's pid and start time. It
  clears the note only after the end the next section proves; an end it
  cannot prove replaces the note's processes with those not proven to have
  exited. When the proof cannot cover every process, the note says
  `inconclusive`: the count is above the members the job reported joining,
  the count could not be read, or a member has no usable identity (a root
  rolled back before its identity was taken). That holds even when the same
  proof also names known processes; they stay in the note for diagnosis,
  but their exit never clears it. Only the lease's owner ever writes or
  clears the note. Every
  acquisition, abandoned or not, reads the note right after it owns the
  lease. Windows may have destroyed the mutex with its last handle, so a
  fresh acquisition can follow a crash, and the note is what tells. A note
  left standing means the last holder did not finish: the new holder waits
  until every process the note names has exited, then for the floor, then
  clears the note and runs. While it waits, the head's line names those
  processes. An `inconclusive` note is never cleared by a holder: the queue
  stops, as for an unreadable place, the line says
  `GPU 작업의 끝을 증명하지 못했다 — 확인 후 지우기` with the note's
  path, and the owner deletes the note. Nothing is timed out.
- A place gives the order. A second mutex, `Local\wiki-agent-gpu-queue`,
  guards taking one: under it the taker reads and raises a counter in
  `gpu\next` and creates the place file `gpu\queue\<counter>` with
  `CREATE_NEW` and no sharing, held open while it waits and runs. The
  counter, not the clock, orders places, so a paused taker or a clock change
  cannot put a place ahead of one taken before it.
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
  so the runner watches the shell. When either ends, the runner ends its
  command as the normal end below does.
- It creates the command inside a job of its own from its first
  instruction, as `owned_job` creates a look's process, so no child can
  start outside it, and the job ends every member when it closes. A process
  that cannot be created inside its job starts nothing and says why.
- It writes the holder note, then checks both watched processes. Only if
  both live does it resume. One that ends after that check is seen by the
  runner's wait on both handles at once, and the runner ends the job then;
  between that check and the end of the job the command can run for as long
  as Windows takes to report the exit. On a server launch, an accepted stop
  and the resume are serialized by the existing launch gate
  (`desktop_session`), so an accepted stop either comes before the resume
  and nothing runs, or after it and ends the job.
- The normal end, whether the command finished, was cancelled, or its shell
  or session ended: the runner ends its job with `owned_job`'s proof, not a
  zero count, since Windows can report no active process while one is still
  exiting. The proof is a successful termination, an exit proven for every
  member the job reported joining, and an accounting total that covers every
  one of them. Only then does it clear the holder note, release the lease
  and drop its place. If the proof does not come, it writes the processes
  not proven to have exited into the note, marked `inconclusive` when the
  proof could not cover every process, then releases the lease and drops
  its place, and its output names them. The next holder waits on
  them as on any leftover note, visibly, and nothing is timed out. So no
  thread has to stay alive holding the lease for a job it could not end.
- The server's launches end the same way, on the measurement worker's
  thread that owns the lease. The PC program's look (`desktop_session.look`)
  already proves its job's end with `owned_job` and records the members it
  learned. When that proof is not clean, the look closes its job and
  raises, as today. The worker catches it, writes the members the look's
  record does not prove exited into the note, or `inconclusive`, and only
  then releases the lease. The backend's start (`oneshot_measure_app.served`), which today
  closes its job and waits for the root only, moves to the same proof and
  the same note.
- However the runner itself ends, Windows closes its handles: the job's
  members are ended and the lease is abandoned or destroyed, with the note
  left standing. The next holder acts on the note as above. ponytail: a
  descendant of the noted root is ended by the same job close but is not
  waited on one by one; the floor is what keeps the next job off memory it
  still holds.
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
   - The watch runs from the send until the check of that turn has run its
     last server command. `oneshot_build.checked` reads it first, before any
     check runs, which judges the build session's own window. The checks
     that then run commands on the server read it again right before they
     save anything: `tested` after the contract-test command, `built` after
     the gate and `serve_check`, `oneshot_front_build.built_front` after
     its build and looks, and S8's `oneshot_pr.readied` after its final gate
     and release build, before it writes the gate's result and the final
     `HEAD`. So a gate test that writes there and exits is still seen.
     `checked`'s two returns that run no check, the hand-over
     (`HANDOVERS`, S8's PR text) and the stop for a question, read it before
     they save; a change refuses the turn in their place. `changed` or `incomplete` at either read refuses the turn
     with the folder and the paths, before any stage is saved, and that is
     the next build turn's note.
   - Every other way a check ends counts too: a failed or stopped gate, a
     failed contract-test command, a failing frontend look, an error. When
     `check` returns or raises, `checked` closes the watch and judges it
     before it writes the outcome or schedules the next turn. A change found
     there is added to that outcome's note, so a gate that writes there and
     then fails still reports it, and the watch's hold is settled on every
     path.
   - A turn that ends without reaching `checked`, failed or stopped, is
     read at `work.run_turn`'s finalization, beside `oneshot_needs.settle`.
     Its result goes into the next build turn's note.
   - A refused or noted turn does not hold the next one: each turn is judged
     on its own window, and the first clean turn passes.
   - A hold found from a server that stopped mid-turn stops the run for the
     owner with `주인 데이터 폴더를 감시하지 못한 구간이 있다 — 확인 후
     재개`. An unwatched window is never taken for a clean one; the owner's
     resume clears it.
   - The server's measurements of the product, the PC measurement and the
     trial, watch the same roots over their run. A change fails that
     measurement with the folder and the paths, as a failed metric does.
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
  unreadable place stopping the queue, the floor holding the head, a
  leftover holder note acted on after an abandoned lease and after a
  destroyed one with no waiter at the crash, a server holder's note, and
  the server's launches waiting and stopping in it;
- one folder seen by a shell with another `TEMP` and by the gate's
  environment, and a process of another account running nothing and
  creating nothing, with no live queue handle of the owner's at the time;
- the runner ending its job when its shell, its session or itself ends, a
  shell ending between the last check and the resume, a process that cannot
  be created in its job starting nothing, a server stop on either side of
  the resume, a job that reports no active process with an exit unproven
  leaving those processes in the note and the next holder waiting on them,
  and the backend's start ending by the same proof;
- a PC measurement whose look raises on an unclean proof, through
  `desktop_session.look` on its own desktop, leaving the unproven members
  in the note before the worker releases the lease;
- on one worktree, an owner's turn after a build turn and a build turn
  after an owner's turn each starting the CLI again, marked only for the
  build;
- Codex admitted with `--now` holding the lease for its run, refused when a
  place waits, when a place is published during its step, for a leftover
  note and for the floor, and two Codex runners racing while the first
  drains, its note left in place, and refused for a note whose root has
  exited while a child it names lives;
- a proof failed on the job's count with every known member gone writing
  `inconclusive`, the queue stopping on it with its line, and running again
  once the owner deletes the note; a missed join with a named member still
  alive, the queue still stopped after that member exits; a rolled-back
  root with no identity writing `inconclusive`;
- the GPU unreadable, RAM unreadable, and two adapters;
- each spelling of a root refused on both hosts, the watch's three results
  on an answered, a failed and a stopped turn, a root replaced during a
  turn, a gate test writing there and exiting before `serve_check`, a gate
  writing there and then failing, a failing frontend look, S8's final gate
  writing there, a hand-over and a question stop after a change, the turn
  refused before any stage is saved, the first clean retry passing, a
  leftover hold stopping the run for the owner, and a server measurement
  failed by a change.

The full gate passes once after the reviewer allows the merge.

## 15b — the build's loop measures on the server, watched when wanted

### First, the check that gates 15b

Before anything below is built, with the owner's go-ahead for that step, on
this PC, with the run's actual debug product started on a desktop of its own:

1. `PrintWindow` gets 60 s of frames of its window while a video plays, at
   least 100 of them different.
2. Muting as the measurement will mute (below), with playback started after
   launch the way the measurement script starts it: a loopback capture of
   every active output device hears nothing from the program from its first
   sample on, while a process loopback capture of the program's processes
   hears its sound.
3. Stopping both captures leaves the program and its measurement running.

If any of the three fails, 15b stops there and goes to the owner.

### The build asks the server to measure

A build turn may end with a `measure-request` block, a fenced block as
`done-report` is. It names nothing: a trial runs the contract's script
whole, in `auto` mode, as the automatic measurement does, so its `person`
metrics stay unmeasured and no metric is selected or skipped. The script's
outside calls happen as in any automatic measurement.

The block closes the turn. It joins the closing blocks every build turn sets
(`oneshot_build.Turn`, beside `done-report` and the stage's own block), so a
background task still running after it is left over and not waited on, as
after a `done-report`. Before the trial is dispatched, the server closes the
worktree's cached CLI (`ChatSession.close`). That ends the CLI's job, and
with it every background shell and GPU runner the turn left. This is the
crash path on purpose, not the normal end: a runner ended this way writes
no proof, its own job closes with it, and its holder note stays. The trial,
or whoever takes the lease next, waits on that note as on any other, with
the crash path's stated descendant limit. So the trial never waits behind
the build's own leftover for longer than its processes take to exit. The
next turn on the worktree, the build's or the owner's, starts the CLI again
with `--resume`.

An owner's words during the turn (plan 14's steer) keep today's protection
unchanged. Any steer in a turn makes a closing answer wait for every
background task (`ChatSession`'s `_steered`), because the provider gives no
sign that a result answers the steer rather than coming from before it:
Claude can replay the steer and then send the old result
(`test_a_closing_answer_does_not_drop_a_steer_already_read`). So a steered
turn that closes with `measure-request` while background tasks run waits
for them, and the card shows the turn waiting on them. Its request is read
when the turn ends, and the trial is dispatched then. ponytail: a helper
that never ends holds such a turn until the owner stops it in the work
panel, and a stopped turn's request is dropped, so the next build turn asks
again; narrowing it needs a provider signal tying a result to the steer it
answers.

Where it is accepted: a build turn whose effective stage is `build`, or a
repair turn sent from `measure`, in a run whose `HEAD` holds the measurement
plan (`oneshot_measure.planned`). Elsewhere the block is refused with that
reason in the next note.

The server answers with a trial measurement:

- At acceptance it records the commit to measure: the worktree's `HEAD`. An
  uncommitted tree refuses the request, naming the files; the build commits
  what it wants measured, diagnostic logging included.
- The trial worker takes the worktree hold, as the run's measurement worker
  does (`oneshot_review`), and checks that `HEAD` is still the recorded
  commit and the tree is clean. If not, the trial ends `superseded` and the
  next build turn is told; nothing is built from another commit.
- Only a plan with `app` (a PC program) has trials. A `serve` contract names
  no data folder, so a trial of it could not be kept apart from the run's
  measurement; in such a run the block is refused with that reason until
  its contract names one.
- The worker keeps the hold for the whole trial, through the script's run
  and the check of its result, as the run's measurement worker does, so the
  product and the script both come from the recorded commit.
- Under the hold it runs the run's own gate command (`gate_cmd`), which is
  what builds the program today (`oneshot_desktop.built` reads its output).
  It checks again that `HEAD` is the recorded commit and the tree is clean,
  as `specs.judge` does after a gate. Then it checks the output with
  `built` and freezes it to the trial's own folder, `measure-app\trial\app`,
  exactly as the run's measurement does. Nothing in the output folder is
  moved or removed.
- ponytail: the trial trusts the gate's output as the run's own measurement
  does today; a gate that passes without rebuilding leaves an older program
  in place, and the trial measures it. So the trial's record and its note
  carry the program's last-write time beside the gate's start, and the
  build sees when it measured a program its gate did not write. Proving the
  output's commit is for the measurement and the trial together, later.
- A failing gate ends the trial with the gate's tail and measures nothing.
- The copy never replaces `measure-app\app`, which the owner's round may be
  waiting to run.
- The trial's program keeps its data in `measure-app\trial\data`, its own,
  passed as the contract's `{data}`, kept from one trial to the next as
  `measure-app\data` is kept between measurements. It starts empty and
  inherits nothing, so its first trial downloads the runtime once. Nothing a
  trial writes reaches `data`.
- It takes a GPU place and the lease as any measurement does.
- No other build check runs first. A trial is the build's diagnosis and
  never passes or fails the run.

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
measurement command. The contract's side is the command the server would
run: rendered as the server renders it (`oneshot_build.run_as`, the recorded
tool paths and PowerShell's call operator included). The agent's side is its
call. Both are split into commands at `&&`, the chain the contract allows,
and each command into shell words as `guard` does. Only the contract's
command that holds `{out}` is the measuring one; the chain's other commands,
a build or a setup step, are not matched and pass. 15b makes this a rule of
the contract rather than a guess: the contract page says the one command
that receives `{out}` is the measurement itself, and the commands before it
only prepare; `oneshot_measure_plan` checks that `{out}` sits in the chain's
last command. An existing contract that breaks it is refused at its next
check with that reason, as any malformed contract is, and the build
rewrites it. ponytail: a contract that measures in an earlier command and
only exports in the last one still passes the check; it breaks the page's
rule, and the variable refusal above still applies to it. Each of the agent's
commands is matched against that measuring command, whether it runs alone
or inside a chain of its own. A program word on either side is compared by its
canonical executable: the recorded tool path for a declared tool, else the
file `PATH` or the explicit path resolves to, links resolved, case ignored.
Every other word matches literally, except the one word holding `{out}`: it
matches any word with the same text before and after the placeholder and
something non-empty in its place, so `--out={out}` matches `--out=C:\x` and
nothing else. So the call the server itself makes, with a real folder in
place of `{out}`, matches. Reading the contract or the script passes, and so
does a test that sets the variable in its own code.

A command that reaches the script through another runner is not seen. The
request above is the faster path and the one the prompt names; the refusal
catches the direct call, the one the 2026-11-26 turn made four times. Tests
use that exact call shape.

### The logs, for the next note

- What is kept: the program's output, already captured and masked; and,
  under the measurement's data folder, files whose names end `.log`, `.txt`
  or `.jsonl`. At the start each such file's identity (volume serial and
  file index) and size are recorded. At the end, a file with the same
  identity and at least that size keeps only the bytes after it, so an
  append log brings no earlier run's lines. One with the same identity but
  smaller was truncated and rewritten, and is kept whole. One with a new
  identity, or none at the start, was created or replaced during the
  measurement, and is kept whole. A rotated-away file under another name is
  kept only if its own name matches and its identity is new. Newest first,
  up to 5 MB in all, masked as the output is. ponytail: a log truncated in
  place and rewritten to its old length or past it looks like an append, and
  its new lines before that length are lost.
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
- Muting covers every audio session of the program's job on every output
  device, from before the program runs to its end. Before it resumes the
  program, the server registers for new sessions (`IAudioSessionManager2`,
  `IAudioSessionNotification`) on every active render endpoint, and for
  endpoints that are added, become active or become default during the run
  (`IMMNotificationClient`: device added, state changed to active, default
  changed), registering on each as it appears. On each registration it also
  reads the endpoint's sessions already there. It mutes each session of a
  job member as it is created or found, child processes included, and reads
  the mute back. A session of a job member found already playing before its
  mute was verified fails the launch: sound may have reached the owner. So
  do a session it cannot mute, a mute it cannot read back as set, and an
  endpoint it cannot register on. Each fails with its reason, and the
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

- a `measure-request` accepted only where stated and refused in a `serve`
  run, measured as a trial of its recorded commit with the tree held
  through the script, built by the gate into its own copy and data, ended
  `superseded` when another commit took the tree, ended by a failing gate
  and by a first build on a clean checkout with no output passing, refused
  on an uncommitted tree, its card before `built_head`, its result back as
  the next build turn after an end, a wrong input, a stop and a restart, the
  program's last-write time and the gate's start in its note, nothing in
  the output folder moved, and the run's measurement, `measure-app\app` and
  `measure-app\data` unchanged;
- a turn closing with `measure-request` while a background helper and its
  GPU runner with a child still run: the turn settles, closing the CLI ends
  them with the runner's note left standing, and the trial waits on that
  note and then runs; a steered turn closing with `measure-request` still
  waiting for its background tasks, its request dispatched when they end,
  and the existing old-result-after-replay test unchanged;
- a contract whose `{out}` is not in its chain's last command refused with
  the reason;
- each spelling of the variable refused, the contract's command refused as
  the server renders it, an installed interpreter's recorded path included,
  with a standalone `{out}`, with an attached `--out={out}`, and inside a
  `&&` chain of the agent's, the contract's build step alone passing, a
  different option in the `{out}` place passing, and a read of the contract
  passing;
- the logs cut at each file's starting size, a truncated and a replaced
  file kept whole, refused for a reparse point or a second link, masked,
  copied, named in the note and removed after;
- only the three launches on their own desktop; a session created after
  launch, a child's session and a session on a second output device muted;
  an endpoint added, or an existing one becoming active, during the run
  registered; a session found playing before its mute, and a mute that
  fails, ending the launch;
- [보기] refused through the tunnel and for a measurement not running,
  bound to its measurement, and closed on each of its three ends.

The full gate passes once after the reviewer allows the merge.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 15a | The machine line, the load line, the two GPU-job kinds, the session's own processes and the refusal behind them, the Claude queue and the Codex run-now-or-refuse, the lease and the queue's order shared with the server's launches, the runner, the owner's data folder refused and watched over every build turn and server launch, the prompt section | Not started |
| 15b | The check that gates it, `measure-request` and the trial measurement with its own state and card, the build's own measurement refused, the logs for the next note, three launches on their own desktop and muted, [보기], the pages rewritten | Not started |
