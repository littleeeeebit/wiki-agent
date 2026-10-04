# Development and checks

How the parts fit and which document answers what is in
[architecture](architecture.md).

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check tool
python -m pytest -q tool
python tool/lint.py --check
npm --prefix web ci
npm --prefix web run lint
npm --prefix web run build
powershell -ExecutionPolicy Bypass -File tool/build_android.ps1
python tool/graph.py
python tool/omm_scan.py
```

Inside a virtual environment, run with that environment's Python.
`graph.json` is generated from the shared rules and excluded from git.
Building the map against real projects can pull in those projects' paths and
state, so the result is not added to the public copy. Regenerate the map file
with the `python tool/graph.py` command above.

The automatic checks use a temporary project. Real users' conversation records
are not test input. They confirm settings merging, reinstalling, paths with
Korean characters and spaces, and settings separation between same-named
checkouts. A real OAuth sign-in, the host's automatic events and the answer
quality review are not part of these results.

Having changed a hook implementation, follow the
[host confirmation procedure](hooks-setup.md) as well. Before publishing, run
the [public-copy update procedure](publishing.md) over the files and the git
history.

Terminal translation is manual: select output, remove private values in the
preview, then confirm sending it to the external translator. Merely enabling
the Korean overlay never uploads terminal output. Agent and Review progress
continue to use the automatic overlay.

The code-change disclosure lists every changed file, including binary and
untracked files, using Git's full numstat inventory. Each file has its own
keyboard-accessible toggle and loads a separate patch when expanded. The
200,000-character preview limit applies to that file, so a large earlier
patch cannot hide later files. Symlinks and large untracked files remain in
the list with an explicit content omission. Paths are selected from that
inventory and passed as literal Git pathspecs; the index is never changed.

Task model controls include a FAST switch, initially OFF. It requests native
CLI processing speed, independently of reasoning effort: Claude receives
process-local `--settings` with `fastMode`, and Codex receives `service_tier`
plus an explicit thread/turn service tier. OFF explicitly requests standard
speed even when the CLI account defaults to fast. A change resumes the same
conversation on the next turn. Queued instructions and the task's saved cell
carry the choice into subsequent implementation fixes. Unsupported models
disable the switch. An unresolved Claude default also disables it; choose
the Opus alias or a documented Opus 4.8, 5 or 5.5 identity to request FAST.
Unknown future model names do not imply support. Provider account access, billing and fallback remain
provider-owned. These mappings follow the
[Claude fast-mode documentation](https://code.claude.com/docs/en/fast-mode) and
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Local launch and protocol tests verify requested settings; they do not
measure paid account speed or prove that a provider granted the requested tier.

The center pane's App structure tab displays the selected repository's `.omm/`
documents. A repository without them offers an Add .omm button; reading the
tab does not create or regenerate documents. Explicit creation uses a native
model CLI to read source behavior, then the installed `omm` CLI to write
descriptions and labeled Mermaid diagrams. Install `oh-my-mermaid` first.
`python tool/omm_scan.py --repo <path> --model <model>` requests a manual scan;
without arguments it scans wiki-agent with the default Claude login.
Generation preserves existing maintainer fields and seeds staged CLI writes
with existing OMM metadata. Publication includes native history, timestamps,
update counts and parent children; unchanged fields are not rewritten. Staged
output is validated before publishing it. Diagrams fit the pane and can switch to natural
size for scrolling. Frontend builds never run the scanner.
Automatic generation runs once after a confirmed PR merge and completed
checkout cleanup, using that task's saved model. Busy or unpublished checkouts
wait; failure is recorded without repeated paid requests. Shutdown cancels
analysis, leaving the persisted request pending. Five-second polling only
reads saved documents. The wiki and evidence maps keep their own purposes.

Frontend builds retain previous content-hashed assets because an open window
can still import an older Mermaid chunk. If a chunk is already missing, the
diagram error offers a screen reload to load the current build. A missing
JavaScript asset is separate from a repository without `.omm`. Retained build
assets may be pruned while the app is closed if storage becomes material.

The [mobile companion](mobile.md) shares the desktop server. Paired phones
keep a focused portrait pane and a compact PC-style landscape workspace. APK 0.1.3 selects screen orientation and
layout together from its native menu without orientation detection. Portrait
uses bottom navigation; landscape shows a foldable task rail plus conversation
and selected task, with optional full-width focused panes. These styles are remote-only; local PC windows retain their
existing breakpoints, including a single pane through 1100px. Secondary task settings and diff contents are disclosed
on demand. `python -m pytest -q tool/test_mobile.py`
checks pairing and the WebSocket bridge. With Playwright available,
`python web/tests/mobile_browser.py --live-tunnel` verifies phone layouts and
real HTTPS transport against a synthetic fixture, then stops its own tunnel.
`python web/tests/mobile_sync.py` checks two-client live conversation and
reconnect against real record/feed producers over a loopback-only TLS relay;
its model is stubbed and it never publishes user data. `tool/test_sync.py`
checks identifier-only invalidations and feed replay/restart cursors.
The Android build runs release pairing-link tests and lint, then verifies
signing, alignment and permissions before writing `artifacts/wiki-agent.apk`
and `artifacts/wiki-agent.aab`. Signing-key ownership, Galaxy installation
protection, prerequisites and device acceptance are in the same mobile guide.

The agent pane reads `GET /api/work/diff?path=…` every second during a turn
and every five seconds while idle. It includes staged, unstaged and untracked
files without changing the index. The collapsed panel replaces the right-side
specification summary above the task tabs, with added/deleted lines and file
counts. Its position is independent of transcript scrolling and the selected
tab. A task's base persists across turns,
commits and restarts; its current merge base excludes integrated upstream changes.
Git supplies totals independently of the capped preview. Binary files and unread
symlink targets are identified without claiming text line counts. Questions in
Agent, Next Task and Planner share chapter cards, Markdown examples and the
Korean overlay; submissions retain original option labels.

`GET /api/providers/{claude|codex}/usage?path=…` reports tokens and provider
status for the task model row. `GET /api/providers/usage` gathers account limits
from work, conversation and review sessions for the rail footer. Each provider's
quota windows are displayed separately, with only the reset date and time.
Codex reads its public account quota through app-server, cached for one minute.
Claude reads its existing CLI OAuth login's account quota, cached for five
minutes, including failures. It never refreshes or writes credentials. SDK
rate-limit events supplement this reading; missing utilization does not erase
a measured percentage for the same reset window. SDK events use a fraction,
while the account endpoint supplies a percentage. Missing account fields are
shown as unavailable.
The response's `provider` names the attached session even if the next-turn
model selector differs. Diff stdout is read to 200,001 characters and the Git
child is stopped at the limit; a ten-second watchdog reaps stalled children.
`python -m pytest -q tool/test_agent_panel.py` checks real Git changes, question
replay and both providers' event fields. Restart the app after rebuilding the
screen so its Python server loads the updated routes too.

New tasks create a branch with `git switch -c` in the selected repository;
they do not create another checkout or a `<repo>-worktrees` folder. Only one
branch can run in a checkout at a time. Starting or opening another branch
waits for active work/review. New task creation carries compatible staged,
unstaged and untracked changes through Git's ordinary switch; Git refuses
overwrites without committing or stashing anything. Explicitly reopening an
existing task still requires a clean checkout. Existing linked
worktrees remain readable, and external PR review can explicitly use one.
The original repository cannot be deleted through worktree cleanup.
New tasks started from another managed task use its saved base branch, so the
previous task's commits do not leak into the next PR. A restart releases an
interrupted survey's checkout reservation without replaying its writes.

Implementation sessions use full access: Claude starts with
`--dangerously-skip-permissions`; Codex uses `approval_policy="never"` and
`sandbox_mode="danger-full-access"` at process, thread and turn boundaries.
An old saved permission toggle cannot downgrade these sessions. Questions
about the intended result still wait for answers. Read-only review and Cloud
verification keep their separate execution scopes.

Implementation and repair agents may commit, push and open their task PR.
Reused PRs receive the current title, requirements and completion evidence.
By default independent review approval and a passing final gate proceed
through the existing guarded merge automatically. `auto_merge = false` in
the loop settings retains manual merge. A queue waits for confirmed merge.
After merge, a clean shared task checkout returns to and fast-forwards its
base. The reviewed local branch is deleted only after its content is verified
on that base, with an atomic expected-head check. The remote branch is deleted
with a lease on the reviewed head. Busy or dirty checkouts, newer commits and
unrelated branches retain a visible cleanup-pending task. Cleanup is persisted
and retried across restarts; a merged task disappears from the rail only after
cleanup completes. Drafts remain under Start pending.
If that base is already open in a sibling worktree, the selected checkout uses
one reusable `wiki-base/<checkout-id>/<base>` branch tracking the remote base.
This creates only a Git ref, not a checkout. It never moves the sibling's base
ref or files. Survey handover validates this branch's actual upstream base and
repository too. New tasks may use a fetched fast-forward of their saved base
without changing an occupied branch.
Starting from a reopened merged task refuses an ahead or divergent saved base,
or an unverified upstream, before creating any branch. Resolve that history
explicitly; unpublished sibling work is never silently imported into the task.

A manually published PR whose branch is already open in the selected checkout
reuses that directory after verifying its clean state and exact PR head. It
keeps the branch-derived task ID, rather than using the repository folder name.
Busy checkouts and unpublished edits are refused before attaching metadata.
Cleanup closes the review cell and removes only that PR's `raw/review/<repo>/<pr>`
artifacts. Requirements, round verdicts and deferred P2 remain in the spec.

Errors are recorded locally in `raw/errors.jsonl`: HTTP refusals and exceptions,
agent/query failures, review startup and cleanup failures, uncaught server/thread
exceptions, and browser errors/rejected promises via `POST /api/errors`. Records
carry UTC time, source and workflow identifiers; exceptions include stack traces
without frame locals. Request bodies, prompts, headers and URL queries are not
recorded. Known credential formats and secret environment values are redacted.
Files use UTF-8 without BOM and rotate at 2 MB with three backups. Logging failure
does not change the original response or workflow result. Browser reporting is
limited to twenty errors per loaded page. These local records are not uploaded.

Investigation and verification scope are recorded in
[task lifecycle recovery](research/task-lifecycle-recovery.md).

The server retains gate execution, PR recovery and independent review.
Ordinary completion and planner publication stop at the published PR until
the person explicitly starts Review Loop. Once requested, rounds and restart
recovery remain automatic within that loop.
An unfinished correction does not start another review on the same head.
Empty answers are failed work turns; incomplete reports get one continuation
in the current round, then a visible stop if completion is still unverified.
Continue resumes the pending correction before a new review. Claimed fixes
require a new commit; evidence-backed disagreement can keep the same head.
One server per hub holds `raw/server.lock` before startup recovery, preventing
a second window or development server from stopping live tasks.
Startup automatically resumes active loops and tasks stopped by a previous
restart, rechecking current checkout, head and pending repairs. User stops and
genuine blockers remain stopped. The minute poller also reattaches active
states without a loop driver, and shuts down with its owning server.
The work prompt and review skill explicitly reject manual per-round clicking.
`python -m pytest -q tool/test_loop.py tool/test_runtime.py tool/test_agent_panel.py tool/test_suite.py`
checks correction ordering, automatic restart and orphan recovery, process
ownership, empty-answer failure and completion handoffs after publication errors.

The Suite tab beside App structure lists the selected repository's live cells
and 100 recent executions from existing work, planning, review and conversation
records. It refreshes every two seconds, filters by status and expands model,
time, prompt, steps, answer and error details. Existing records without new
metadata keep an explicit unknown label. An unmatched historical instruction
is interrupted, not completed. Task cells link to their Agent or Review pane;
portrait phones also have a Suite header button. `test_suite.py` checks project
isolation and live-to-history transitions; `web/tests/task_browser.py` checks
filters, polling, details, task links, recovery from read errors and mobile fit.

An idle task's requirements can be edited in its spec panel. During a turn,
later instructions can be recorded through a `spec-update` block before the
completion report. Revisions preserve prior requirements and invalidate old
review approval; the next agent turn receives the current spec.
`python -m pytest -q tool/test_task_workflow.py` verifies these transitions.

Markdown file links, including absolute paths and line numbers, open the
source drawer. HTTP/HTTPS links use the system browser in the Tauri window;
normal browser tabs keep their native link behavior.
`python web/tests/task_browser.py` checks fixed diff visibility across scrolling
and tabs, translated specification reading and original editing, all CLI quota
windows, task tokens, native notification dispatch, compaction progress and
deletion across reload, plus file citations and link dispatch using synthetic data.

The right-side specification summary is hidden; requirements and planning
controls remain in Next Task. Its selected task card also appears when the
original specification message is outside the loaded conversation. Generated
goal, exclusions, completion conditions and decisions use the Korean overlay;
editing and submitting preserve the originals.
The server-owned first completion condition is an executable gate and stays
verbatim, outside the prose overlay.

Task deletion uses `POST /api/specs/{sid}/delete`. It stops that task and archives
its specification in `raw/specs/<repo>/dropped/`. A shared repository, its branch
and uncommitted changes remain. Archived tasks stay hidden after restart, and a
late save from a deleted task cannot restore it or overwrite a new task with the
same name. Legacy worktree deletion retains its existing cleanup behavior.

Questions, execution completion/failure and conversation completion/failure emit
identifier-only notice events on the shared feed. Desktop delivery explicitly
uses Tauri's notification plugin, including permission checks; browsers use their
notification permission and service worker when available. Settings offers an
alert test and permission guidance. Feed cursors carry a process generation;
a reconnect to another generation replays its buffer, including notices
already emitted before reconnecting. The client suppresses duplicate notice
timestamps/sequences and ignores notices older than two minutes.
Codex compaction items and Claude status/
boundary events show start/end steps, retain them on replay and show an agent
banner while compaction runs. Browser checks cover native IPC dispatch with a
stub, not Windows notification-center delivery or a real model's compaction.

The native privacy regression uses Playwright/CDP and a separate fixture app,
not desktop control. With Playwright already available, run
`python web/tests/terminal_privacy.py --app <isolated test exe> --project <fixture project> --task <fixture task>`.
The executable must be built against the isolated checkout containing that
fixture; do not point it at a user's running app. The check starts and closes
its own app, refuses an occupied CDP port, and intercepts translation requests
locally so no test output goes to an external provider.

## Attaching this repository to the rules

This wiki is maintained too, so the same hooks hang on itself. The method is
the one in the [hooks install guide](hooks-setup.md); only `--project` differs,
pointing at this repository. Run it once per checkout.

Start by creating this repository's `.wiki/adapter.toml`. The public copy
keeps wiring files out of git (`tool/test_distribution.py` checks that), so
each checkout writes its own.

```toml
agents = ["claude", "codex"]

[slots]
review_dir = "artifacts/review"
gate_cmd = "python -m pytest tool"
live_cmd = "open a new Claude Code session and a new Codex session, and confirm the hooks actually run"
server_stop = "close the wiki-agent window (tool/app.cmd), or Ctrl+C in the terminal that ran python tool/main"
scratch_dirs = "artifacts/"
```

```powershell
python tool/setup_agents.py --project . --agent both
```

When the target is the wiki itself, the `.wiki/wiki-revision` pin and the
dirty check on the executing code are skipped. The pin answers "which wiki
version is this target bound to", and when the target is the wiki the answer
is always the current HEAD — so it goes stale on every commit and is always
dirty while the tools are being changed. Installing into another project keeps
both checks.

`.wiki/adapter.toml`, `.claude/settings.json`, `.codex/hooks.json` and
`.codex/config.toml` are all excluded from git. They carry absolute paths and
per-machine settings, and the public copy keeps runtime wiring outside version
control. Using Codex means putting `hooks = true` under `[features]` into your
own checkout's `.codex/config.toml` yourself.

On Windows, run the install and the checks from PowerShell. Run inside Git
Bash, `git` resolves to `mingw64/bin`, the installer cannot find Git Bash, and
`tool/test_codex_hooks.py` fails for the same reason. If it has to run in Git
Bash, point `CLAUDE_CODE_GIT_BASH_PATH` at the real `bash.exe`.

Records accumulate only in this repository's `.wiki/`. `corpus.json`,
`graph.json` and `decisions/` are per repository and do not mix with another
project's `.wiki/`. The root `graph.json` and `raw/` are separate — hub assets
used by the chat screen.
