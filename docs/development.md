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
Codex reads its
public account quota through app-server, cached for one minute; Claude uses
its rate-limit stream events. Missing account fields are shown as unavailable.
The response's `provider` names the attached session even if the next-turn
model selector differs. Diff stdout is read to 200,001 characters and the Git
child is stopped at the limit; a ten-second watchdog reaps stalled children.
`python -m pytest -q tool/test_agent_panel.py` checks real Git changes, question
replay and both providers' event fields. Restart the app after rebuilding the
screen so its Python server loads the updated routes too.

New tasks create a branch with `git switch -c` in the selected repository;
they do not create another checkout or a `<repo>-worktrees` folder. Only one
branch can run in a checkout at a time. Starting or opening another branch
waits for active work/review and refuses uncommitted changes. Existing linked
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
After merge, a clean shared task checkout returns to and fast-forwards its
base; busy or dirty checkouts and unrelated branches remain untouched.
If that base is already open in a sibling worktree, the selected checkout uses
one reusable `wiki-base/<checkout-id>/<base>` branch tracking the remote base.
This creates only a Git ref, not a checkout. It never moves the sibling's base
ref or files. Survey handover validates this branch's actual upstream base and
repository too. New tasks may use a fetched fast-forward of their saved base
without changing an occupied branch.
Starting from a reopened merged task refuses an ahead or divergent saved base,
or an unverified upstream, before creating any branch. Resolve that history
explicitly; unpublished sibling work is never silently imported into the task.
The server retains gate execution, PR recovery and independent review.
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

Task deletion uses `POST /api/specs/{sid}/delete`. It stops that task and archives
its specification in `raw/specs/<repo>/dropped/`. A shared repository, its branch
and uncommitted changes remain. Archived tasks stay hidden after restart, and a
late save from a deleted task cannot restore it or overwrite a new task with the
same name. Legacy worktree deletion retains its existing cleanup behavior.

Questions, execution completion/failure and conversation completion/failure emit
identifier-only notice events on the shared feed. Desktop delivery explicitly
uses Tauri's notification plugin, including permission checks; browsers use their
notification permission and service worker when available. Settings offers an
alert test and permission guidance. Codex compaction items and Claude status/
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
