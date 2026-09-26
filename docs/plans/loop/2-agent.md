# Phase 2 — Agent's Deferred Tasks

The relationship between the overall design and the phases is in [Overview](0-overview.md)].

Goal. Modify the work session to support long-running tasks that phases 3 and 4 will introduce. A turn lives longer than a single request, and the screen reattaches to the running turn after a refresh. What was allowed and denied is recorded. Introduce "Allow for this session" so the same write is not asked every time. Codex runs the focus session on the same `app-server` as the write session and reports the token count.

## Agreements with the User

2026-09-25.

| What | Agreement |
| --- | --- |
| When closing the window | Reconnection only connects via refresh, webview reload, or reopening the browser tab. Closing the app window shuts down the server and stops the turn. If there is a running turn, confirmation is requested before closing. No server that runs separately from the tray. |
| Scope of "Allow for this session" | File writes (Claude `Edit`·`Write`·`MultiEdit`·`NotebookEdit`, Codex `fileChange`) are per tool. Commands (Claude `Bash`, Codex `command`) are only for the same command with the same `cwd` up to the text. |
| Before Codex read session | Only focus sessions are moved to `app-server`. Easy explanations (`explain`) are left in `exec`. In `app-server`, there is nothing corresponding to `--ignore-user-config`, so the user `config.toml`'s hook, MCP, and profile are mixed. |

"The loop keeps running even if the window is closed and reopened" in the overview has been corrected according to the first decision. The loop survives refreshes and stops when the app is closed.

## First — Order of Planning Documents

`session_state.plans` lists documents under `docs/plans/` in reverse order of the path and reads only the first two (`MAX_PLANS`) (`tool/session_state.py:111`). When there was only one series folder, it meant the latest series should come first. Now there are eight phase documents in `loop/`, so `7-verify.md` and `6-screen.md` come first. SessionStart shows the rows for phases 7 and 6, not the next phase 2. Candidate materials for phase 3 also read the same function.

Correction. Between series folders, the latest is first as it is now. Within one folder, the earlier number is first. Then `0-overview.md` and the remaining earliest phase document are read.

Test. Set up `a/0-overview.md`, `a/1-x.md` (all finished), `a/2-y.md`, `a/7-z.md`, and see if `plans` outputs `0-overview` and `2-y`. It should be red in the current code.

## Turn Reconnection

### Current

A turn is the response body of `/api/work/say` (`tool/main/work.py:256`). If the screen disconnects, the body generator closes, and `ChatSession._say`'s `finally` closes the process of the unfinished turn (`tool/agent/chat_session.py:442`). Disconnection is equivalent to stopping. Noise in the worktree (`_busy`) is also released when the body ends or is collected (`query.held`).

### Changes

Detach the turn from the response and run it in a thread. Events are accumulated in a buffer kept for each worktree, and the response tails that buffer.

| Location | What |
| --- | --- |
| `Run` | The current turn of one worktree. `turn` (turn id, `uuid4().hex`), `session_id`, `events` (in order), `done`, `threading.Condition` for waking up. |
| `_runs: dict[str, Run]` | Worktree path → last turn. Remains until the next turn starts. A screen reattached immediately after finishing also receives the end. |
| `tail(run, after)` | Streams from the next event after `after`, and waits at `Condition` if there is nothing new. If it is `done` and all have been streamed, it ends. Even if the screen leaves, nothing happens to the turn. |

One event adds `seq` (sequence number in buffer, starting from 0) and `turn` to the current shape.

Endpoints.

| Path | Action |
| --- | --- |
| `POST /api/work/say` | Captures the worktree the moment it is received (same as now). Launches the turn thread and returns `tail(run, -1)`. Screen code receives it as one request as now. |
| `GET /api/work/events?path=&turn=&after=` | Reattachment. If `turn` is not the turn in the current buffer, 410. The path is viewed as "path with session" below. |
| `GET /api/work/log` | Adds `running: {turn, session_id, seq}` to the current record. If there is no running turn, `null`. |
| `POST /api/work/stop` | `{path, turn}`. If that turn is still running, stop it. If it is a different turn, 409. |

Path with session. `events`, `stop`, and the approval answer (`/api/work/answer`) are viewed not by `ours`, but by whether it is a path where the server has already created a session (key of `_sessions`·`_runs`). That path went through `ours` once when creating the session. Since `ours` is viewed as a list of selected projects (`tool/main/work.py:44`), reattaching to an old turn after changing projects results in 404. The approval answer still only looks at the session (`tool/main/work.py:290`). New instructions, clearing, and deletion are `ours` as they are now. This contract is used as is when phase 4 separates the transition from the running turn (Review Round 2).

Noise is held by the turn thread and released at the thread's `finally`. `work.say` no longer uses `query.held`. If the thread cannot be launched, it is released on the spot. The record (`remember(path, "assistant", …)`) is also done at the thread's `finally`. Turns that finished while there was no screen also remain in the record.

### Stopping

Since the turn runs even if the connection is lost, there must be a separate way to stop it. `ChatSession.stop()` closes the process like the current `close()`. `_drain` receives `__closed__`, the turn ends with `error`, and the reason is changed to "manually stopped". Since the CLI's `session_id` remains, the next turn continues with `--resume`·`thread/resume`.

ponytail: Do not use Claude's `interrupt` control request and Codex's `turn/interrupt`. Closing the process is the same for both hosts and does not lose the continuation. The startup time of the next turn (a few seconds) is the cost. If stops become frequent (phase 4 loop), change it then.

### Screen

- Initial load and refresh. If there is `running` of `/api/work/log`, attach one answer turn that is `pending` after the record and attach as `events?after=-1`. Since the buffer exists from the beginning, the tool bar and approval card are redrawn.
- If the stream breaks (not network or server restart), reattach once with the last received `seq`. If 410, reread the record.
- Judgment of late events. Currently, it is path and `session_id`. Add `turn` to this.
- [Stop] in the header on the right side. Only turns on when there is a running turn.
- When closing the Tauri window. If there is a running turn in `getCurrentWindow().onCloseRequested`, ask "N running tasks will be stopped". Permissions only add `core:window:allow-close`. Browsers do not ask — even if the tab is closed, the server and turn live. Permissions are actually `core:window:allow-destroy`. If there is a close listener, Tauri does not close the window, and the screen closes with `destroy()` (`@tauri-apps/api` 2.11's `onCloseRequested`). `allow-close` alone does not close the window even if confirmed.

### Test

Add to `test_main.py`. All must be red in the current `work.py`.

- Turn lives longer than the response. Discard and collect the response body of `say` as soon as it is received. After the band CLI finishes, there is an answer row in the record, and noise is released then.
- Reattachment. After a few events, `events?after=k` gives everything from `k+1` to `done` without omission. Same when attaching to a finished turn. Different turn id is 410.
- Stopping. After `stop`, `error` event comes, noise is released, and there is no process. The next `say` continues with the same CLI session id.
- If two screens tail one turn, both receive in the same order.
- Even if the project is changed after the turn ends, one can attach to that turn's `events`. Paths without a session are 404.

## Approval Record

### Current

The answer row of the record is up to the tool bar (`tools`) (`tool/main/work.py:282`). Approval only exists on the screen and disappears when refreshed. Phase 6 deferred "what was allowed is only visible as a tool bar when reopened".

### Changes

Write the tool bar and approval in order on one line. Change the `tools` of the answer row to `steps`.

```json
{"steps": [
  {"kind": "tool", "text": "Read · tool/lint.py"},
  {"kind": "approval", "tool": "Write", "text": "Write · tool/x.py", "answer": "allow", "by": "person"},
  {"kind": "approval", "tool": "Bash", "text": "Bash · pytest -q", "answer": "allow", "by": "session"},
  {"kind": "approval", "tool": "Edit", "text": "Edit · ../elsewhere.txt", "answer": "deny", "by": "outside"}
]}
```

| Column | Value |
| --- | --- |
| `answer` | `allow`, `deny`, `none` (turn ended before answer arrived) |
| `by` | `person` (human pressed), `session` (session rule answered), `outside` (denied without asking because it is outside the worktree), `read` (denied because it is a read session) |

Approval's `input` is not recorded. The entire body of `Write` is accumulated in the record. What was used is told by the worktree and commit.

- If a human answers, a `answered` event (`{id, allow, by}`) is accumulated in the buffer. Reattached screens and other windows draw the card as answered.
- Denials for outside the worktree and read sessions are currently `tool` lines (`chat_session.py:340`). This also outputs by attaching `by` to `approval`. The screen draws as a card with the answer finished.
- `tools` of old records is changed to `steps` by `/api/work/log`. The screen only knows one shape.

Test. Run a turn with one allowed, one denied, one outside, and one stopped without an answer, and see if the record's `steps` is in that order and value. The shape of the refreshed screen is viewed by the response of `log`.

## Allow for This Session

### Where the Rules Live

Held by `ChatSession`. Not passed to the CLI.

- If Codex's `acceptForSession` and Claude's `permission_suggestions` are used, the CLI no longer asks. Then it does not go through `_approval`'s outside worktree check (`chat_session.py:338`). Claude's `Edit` allow rule does not distinguish paths.
- The meanings of the two hosts are also different. Codex's command cache and Claude's rules are matched differently. Decide on one here.

`ChatSession._rules: set[tuple]`.

| Request | Rule Key |
| --- | --- |
| Claude `Edit`·`Write`·`MultiEdit`·`NotebookEdit` | `("file", 도구 이름)` |
| Codex `fileChange` | `("file", "fileChange")` |
| Claude `Bash` | `("command", "Bash", input.command)` |
| Codex `command` | `("command", "command", command, cwd)` |

The order of `_approval` is as follows. If outside the worktree, deny. If a read session, deny. If it matches the rule, allow without asking and issue an approval event that is `by: "session"`. If none of the three, ask the human. Outside check is before the rule.

### Lifespan

Rules attach to `ChatSession.id`. If the id changes, there is no rule.

| Event | Rule |
| --- | --- |
| Changing model/effort (`reconfigure`), stopping | Remains. Same object, same id |
| Clearing context, changing CLI, deleting worktree | Disappears. New object |
| Server restart | Disappears. Not written to disk |

### API and Screen

- Add `scope: "once" | "session"` to the body of `/api/work/answer`. Default is `once`. `session` cannot come for read sessions or outside denials (409).
- Approval card buttons are [Allow] [For Session] [Deny]. If it is a command, the middle is "This command for session".
- `/api/work/log` issues `rules`. Shows "Session Allow: Edit · Write · `pytest -q`" in one line below the header and places one [Release]. `POST /api/work/rules/clear {path, session_id}`
- Turns sent by phase 4's loop are also the same session, so they follow the same rules. Same as the safety boundary in the overview.

### Test

With `test_agent.py`'s band CLI.

- If `Write` is allowed as `session`, the next `Write` is allowed without asking. `Edit` asks.
- Even if there is a rule, `Write` outside the worktree is denied. This test is red if the outside check is moved after the rule.
- If `Bash`'s `pytest -q` is allowed, `pytest -q` is not asked and `pytest -q -x` asks.
- Codex `command` asks if `cwd` is different.
- New `ChatSession` has no rules. Remains after `reconfigure`.

## Codex Focus Session to `app-server`

### Current

`ChatSession.app` is only for Codex write sessions (`chat_session.py:151`). Focus sessions launch `codex exec` every turn and connect with `resume <id>` (`chat_session.py:185`). The startup of the first turn is repeated every turn.

### Changes

Change `app` to `is_codex and not isolated`. Focus sessions and write sessions follow one path.

| `thread/start` Argument | Write | Focus |
| --- | --- | --- |
| `sandbox` | `read-only` | `read-only` |
| `approvalPolicy` | `untrusted` | `never` |
| `developerInstructions` | If exists | Focus header |
| Startup argument | None | `--disable multi_agent`. Same as current `exec` |

- If an approval request comes to a focus session, `_approval` denies without asking as now. It shouldn't happen because it is `never`, but the path is left.
- Reduce `exec` path to `explain` only. Delete `resume` argument and non-isolated branch. Keep isolation flag as is.
- After server restart, `session_id` of old records is the thread id created by `exec`. Actually see if `thread/resume` receives it. If it cannot receive it, start with a new thread and leave the `context` row "Codex continuation failed" in the record. Do not lose the conversation silently.

### Things to Check

- Does the wiki hook inject even in `app-server` focus sessions? `exec` followed user settings and hooks. See if there was an injection in a question with `hook_diagnostics`'s record. If not, stop before this and write the cause.
- Time of the second turn. Since the process is alive, startup should be missing. Measure before and after once.

Observed with actual Codex (`gpt-6-astra`, CLI 0.156.0) on 2026-09-25.

| What | Result |
| --- | --- |
| Hook injection | Does it. `app-server` carries `context` injected into `hook/completed` notification. In the connected `ai-nara-shop`, the wiki's `sessionStart` is 15,203 characters, and `userPromptSubmit` put in 13,000 and 6,760 characters. There is no injection in this repository because the old hub views repositories without `.wiki/adapter.toml` as unconnected. Even if the hook is run manually with the same input, it is empty. `exec` is the same. |
| Second turn | `app-server` 5.2s, `exec resume` 8.3s. The first turn is 41s·33s, which is within the deviation of the same question. |
| `exec` thread continuation | `thread/resume` receives the thread id created by `exec`. The conversation of the old record continues. If it fails, the path to leave a new thread and `context` row "Codex continuation failed" is kept as is. |

## Token Count

### Current

Claude carries tokens and costs in `done`. Codex `exec` carries `usage` of `turn.completed`. Codex `app-server` does not carry (`chat_session.py:500`). If focus is moved to `app-server`, focus also loses the token count.

### Changes

Receive `thread/tokenUsage/updated` notification. `tokenUsage` contains `last` and `total`.

- The schema does not say whether `last` is the sum of one turn or one model call. Confirm with actual notifications. If it is one turn, use the last `last`; if it is one call, use the difference of `total` during the turn.
- Confirmed. `last` is one model call. Three notifications came in a turn that used tools. The number of turns is calculated by the sum of `last` that came in that turn. Do not use `total` difference — in the first notification of the continued thread, `total` exceeded 1.3 million, and the server has no way of knowing the `total` before the turn.
- `done.meta.tokens` is the same as the current shape — `in` (`inputTokens`), `out` (`outputTokens`), `cache_read` (`cachedInputTokens`). `reasoningOutputTokens` is added as `reasoning`.
- Costs are not calculated. Codex is a subscription limit and there is no table to multiply fees.

Screen. The query side already draws tokens (`web/src/components/Stream.tsx:152`). Add the same shape to the line below the answer on the agent side (`Agent.tsx`'s `Reply`).

Test. If band `app-server` sends two notifications, see if `done`'s `tokens` comes out according to the confirmed rules.

## Things Not Done

| What | Why |
| --- | --- |
| Reconnection of wiki query turns | One answer is short, and if it breaks, one can just ask again. Easy explanation and translation are attached to one stream, so splitting them is a big task. |
| Turns running even if app is closed | User's decision. If a tray or separately running server becomes necessary, write a plan then. |
| Leaving rules on disk | It is correct to ask again if the server is restarted. The promise of one session is that. |
| OS notification for approval | Decided in phase 4 where the loop stops at approval, or phase 6 where the screen is rebuilt. |
| Buffer size limit | Events of one turn end within the turn deadline. If tens of thousands of `delta` actually appear, bundle them. |

## Confirmation

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- In the window. Leave a turn waiting for write approval and refresh — the card is redrawn and [Allow] works. After stopping the running turn with [Stop], the next instruction continues. `Write` allowed with [For Session] passes as `by: session` next time.
- One focus question, one write turn with actual Codex. Both have tokens in `done`.
- Leave a running turn in the Tauri window and close — a question pops up, and if canceled, the turn continues to run.

## Phases

| # | Phase | What | Status |
| --- | --- | --- | --- |
| 0 | Planning order | Order in `plans`'s folder and test | Done |
| 1 | Reconnection | `Run`·buffer·`tail`, `events`·`stop`, noise held by thread, test | Done |
| 2 | Approval record | `steps`, `answered`, old `tools` conversion, test | Done |
| 3 | Session allow | `_rules`, `scope`, rule release, test | Done |
| 4 | Codex before and tokens | Focus's `app-server`, `exec` to `explain` only, `tokenUsage`, actual Codex confirmation | Done |
| 5 | Screen | Reattach, [Stop], three buttons, rule line, tokens, window close confirmation | Done |
| 6 | Gate | All of the above confirmation | Done |