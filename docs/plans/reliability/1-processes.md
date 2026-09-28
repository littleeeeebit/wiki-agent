# PR 1 — quiet background processes

Remove the flashing console observed during a Jev question in the desktop app.

## Work

Capture parent/child executable paths and launch events during startup, a warm
question, a cold search-daemon question, and answer explanation. Distinguish Git,
Python, Node/agent executables, and an actual PowerShell process. Do not record
credentials or full private prompts.

Trace every caller of the responsible launch boundary. The sidecar already uses
CREATE_NO_WINDOW; its descendants do not automatically inherit equivalent behavior.
Apply platform-specific suppression to background pipe-based children at the
narrowest shared boundary. Retain output, cancellation, exit codes, encoding and
cleanup. Do not change interactive PTY launching. Detached daemon flags require
separate treatment; do not combine flags without checking their semantics.

## Evidence and exit

One focused regression for the corrected launch contract, plus a real Tauri
question showing the prior reproduction no longer flashes. Confirm streaming,
stop and closing the app still collect owned children. Test cold and warm paths.
No root-cause claim before the process capture.

## Scope and rollback

No Jev policy or translation changes. Revert only the launch change if process
lifecycle regresses. Reuse stdlib subprocess options; no window-hiding service.

## Implementation blueprint

Existing entry points below were inspected. The helper name is proposed.

| File / function | Change |
| --- | --- |
| New `tool/common/process.py::background_options()` | Return `{"creationflags": subprocess.CREATE_NO_WINDOW}` on Windows and `{}` elsewhere; no spawning, shell conversion, environment mutation or lifecycle ownership |
| `tool/session_state.py::run` | Add options at the Git spawn, retaining timeout and UTF-8 decoding |
| `tool/search/sources.py::listing` | Apply to the Git source enumeration used during indexing |
| `tool/agent/chat_local.py::CodexServer.__init__` | Apply to background app-server process; retain existing close/reader ownership |
| `tool/agent/chat_session.py::our_worktree` and process start | Apply to Git inspection and pipe-based agent process |
| `tool/main/specs.py::sh, gate` | Apply to background commands without changing trusted adapter shell semantics |
| `tool/main/knowledge.py::promote` | Apply to its direct Git children if they remain outside `specs.sh` |
| Other production subprocess call sites | Inventory with `rg -n 'subprocess\.(run|Popen|check_output|check_call)' tool`; classify before changing |
| `web/src-tauri/src/main.rs` | Keep sidecar flag and interactive PTY behavior; change only if capture implicates another Rust spawn |

The helper is justified by existing independent consumers. Do not build a process
manager. A caller with existing creation flags must merge compatible flags rather
than pass the keyword twice. Exclude `search.spawn`: DETACHED_PROCESS and
CREATE_NO_WINDOW have different semantics. Retain its existing detach/breakaway
and fallback logic unless the capture specifically implicates it.

### Reproduction record

Write a local diagnostic record under `raw/diagnostics/processes/`:
`{scenario, time, parent_pid, child_pid, executable, launch_site, window_seen}`.
Executable basename and call site suffice; command arguments, environment and
prompt text are excluded. Use OS process/window capture if a child disappears too
quickly for a snapshot. Tie the observed window's PID to the launch rather than
guessing from its title.

Run these scenarios once before and once after the correction: desktop startup,
first question with a cold index, second question, answer rewrite, and explicit
terminal opening. The last must still open a usable embedded terminal.

### Lifecycle and failure behavior

The caller still owns process creation, pipes, cancellation and reaping. EOF and
nonzero exit remain errors under existing semantics. Hiding a window must not hide
stderr from the caller or detach an agent so stopping the question no longer stops
it. A launch error follows the original error path; do not retry without hiding
and claim success while flicker returns.

### Acceptance checks

| Case | Action | Required observation |
| --- | --- | --- |
| Windows background child | Intercept the identified Popen/run call | CREATE_NO_WINDOW present once; same argv/cwd/encoding |
| Other platforms | Exercise helper with non-Windows branch | No Windows-only keyword value introduced |
| Cancellation | Stop an active question | Owned process terminates and pipes/readers finish |
| Real desktop | Repeat captured reproduction | No extra console; answer/trace still completes |
| PTY | Open terminal and type a command | Interactive terminal remains functional |
| Daemon fallback | Existing spawn test | Detach/breakaway fallback remains valid |

Run the affected existing agent/search/session tests first. Retain one small helper
contract check rather than repeating its flag assertion in every test. The final
gate follows PR 2 once available. Record the confirmed responsible call sites in
the PR; unobserved candidates are not presented as root causes.

### Delivery order

Capture -> inventory -> minimal common helper/caller correction -> focused checks
-> real window verification. There is no data migration. Reverting the helper and
its callers restores the old behavior without changing Jev state.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Capture and identify responsible child | Not started |
| 2 | Fix shared launch and verify desktop behavior | Not started |

## Sources

[Python subprocess](https://docs.python.org/3/library/subprocess.html) documents
Windows creation flags and startup options.
