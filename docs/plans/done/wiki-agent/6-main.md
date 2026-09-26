# Step 6 — Main and New Screen

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. Ask the wiki in one window, use the answer to assign tasks to the agent in the worktree, approve writing, and use the shell of the same worktree. Main is `tool/main/` and calls the four pipelines only as `__all__`. Once this is written, it deletes `mirror.py` and the old `chat.py`.

## One-liner

It is step 0 of `design-pass`. The screen list comes from these three lines.

```
누가:   이 위키를 쓰는 한 사람
하려고: 위키에 묻고, 그 답을 근거로 작업트리의 에이전트에게 일을 시킨다
성공:   답의 근거를 연 채 작업 초안을 보내고, 쓰기를 승인하고, 결과를 한국어로 읽는다
```

## Agreements with the User

2026-09-24.

| What | Agreement |
| --- | --- |
| Five channels (Progress, Diagnosis, Retrospective, Review, Wiki) | Remain as the focus of the wiki query area. Prompts and records (`raw/chat/<초점>.jsonl`) remain as is |
| "Write to wiki, write to `CLAUDE.md`" in retrospective candidates | Move to work draft. Writing is done by the agent in the worktree and approved by a human. Delete `oneshot` that wrote to the original without approval |
| Tauri scope | Up to execution command. No installation files or Python bundling |

## Structure

### `tool/main/`

| Module | Task | What is moved |
| --- | --- | --- |
| `app.py` | FastAPI app, source check, translation switch, file viewer, map, static files, `main()` | Skeleton of `chat.py` |
| `query.py` | Wiki query — conversation by focus, record, applied rules, easy explanation, discrepancy display, work draft | Channel part of `chat.py` |
| `channels.py` | Focus definition, project/model list | Move `chat_channels.py` as is |
| `work.py` | Worktree list/creation/deletion, one write session per worktree, approval | New |

It launches as `python tool/main`. Since `chat_post.py` writes to the same record file, it is modified to only view `channels`.

`lint` views `tool/main/` as the same main as the root.

- `pipeline_surface` also reads the modules of `tool/main/` — the new main also calls only as `__all__`
- `pipeline_imports` blocks the `main` import of the pipeline — same reason as the root module

### Tauri — `web/src-tauri/`

Rust does only three things.

- Launches the Python sidecar. Picks an empty port and passes it to `--port`, and if that port answers, it opens the window as `http://127.0.0.1:<port>/`. Since the API has the same origin, the screen code is identical to the browser
- The sidecar closes the session and goes down when stdin is closed (`--exit-with-stdin`). Even if the window is closed or the app dies, the pipe closes, so no `claude.exe` remains
- Terminal. Launches a shell with `portable-pty` and connects to xterm.js with `pty_open`·`pty_write`·`pty_resize`·`pty_close` commands and `pty://<id>` events. Python does not know about the terminal

Execution is `tool/app.cmd` (`tool/app.command` for macOS/Linux). If there are changes, it performs `cargo build --release` and launches the window, then releases the console. It takes a few minutes only for the first time. If you connect to `python tool/main` via browser, only the terminal is missing, and the rest is the same.

### Screen

```
┌──────────┬──────────────────┬──────────────────┐
│ 프로젝트 │ 위키 질의        │ 에이전트 세션    │
│ 작업트리 │ [초점] [모델]    │ [모델]           │
│ ● a      │ Q ...            │ 한국어 오버레이  │
│ ○ b      │ A ... f.py:12    │ 승인 [허용][거절]│
│ + 새 작업│ [→ 작업]         ├──────────────────┤
│ 위키 지도│                  │ $ 터미널         │
│ 번역 ◉   │                  │                  │
└──────────┴──────────────────┴──────────────────┘
```

| Area | What | Component brought in |
| --- | --- | --- |
| Left | Project selection, worktree list (branch/changes/session/suggest cleanup if merged), new task, wiki map, translation switch and this month's usage | Replace `ChannelRail` |
| Center | Select focus and ask. Open `file:line` of the answer. "→ Task" for each answer | `Stream`·`Answer`·`Peek`·`Composer`·`Toolbar` |
| Top right | Agent of the selected worktree. Korean overlay on the answer, tool bar and approval are not translated | New. Draft enters the input field here |
| Bottom right | Shell of the selected worktree | New (xterm.js) |
| Wiki map | Replaces the center | `WikiMap` |

Work draft is created by the server (`/api/draft`). It contains the question, answer, `file:line` cited by the answer, applied rules, open plans, and recent decisions, and the last section "To-do" is written by a human. The branch/uncommitted changes that "Handover" used to contain are omitted — since work is done in a new worktree, it is not in the state of the original checkout. The two buttons of the retrospective candidate attach old `WIKI_WRITER`·`CLAUDE_MD_WRITER` instructions to the same draft.

The screen goes through five stages of `design-pass`. Colors are chosen by the user.

## Screen Ownership

Three things of `craft/screen-ownership-before-wiring`.

| What | Judgment |
| --- | --- |
| Which account | Same as step 5. The session holds the server environment at the time of creation and runs with that login |
| Whose is the late arrival | Agent events are `session_id`. The screen accepts only if both the worktree path and `session_id` match. It also carries `session_id` in the approval answer, and if it differs from the server's current session, it is 409. Translation is judged by the generation of `useOverlay` |
| What can be written | The server does not open the path passed by the screen. It only accepts paths in its own list (`workspace.worktrees`). The write session is checked once more by `our_worktree` of step 5 |

## Safety Boundary

Now the server approves writing. Launching only on 127.0.0.1 is not enough — other tabs in the browser can send `fetch("http://127.0.0.1:…/api/…")`.

- If the host name of `Host` is not `127.0.0.1`·`localhost`, reject it (DNS rebinding). It does not look at the port — it is a request that reached this port, and rebinding is revealed by the host name
- If `Origin` exists and differs from `http://<Host>`, reject it
- Every request carries the project (`X-Project`) visible on the screen. If it differs from the server's selection, reject everything including switching — this prevents this screen from writing to a different project after another window changes it
- The translation switch is held by the server. If it is off, `/api/translate` does not call `translate` and returns the original text. The screen also does not send a request, but that is a promise, and the server's is the check |

## Translation Switch

One for the entire app. Writes the last value to `raw/chat/main.json`. Default is on. Next to it, write this month's usage and limit of `translate.usage()`. The English version of the hook has nothing to do with this switch.

## Things to Delete

| What | Instead |
| --- | --- |
| `tool/chat.py`, `tool/chat.cmd`·`chat.command`, `tool/test_chat.py` | `tool/main/`, `tool/app.cmd`, `tool/test_main.py` |
| `tool/mirror.py`, `mirror.cmd`·`mirror.command`, `test_mirror.py`, `docs/mirror-setup.md`, `Mirror.tsx` | Agent session overlay |
| `tool/chat_channels.py` | `tool/main/channels.py` |
| `/api/decide`, `oneshot` | Work draft |
| `/api/handoff` | `/api/draft` |

Paths of `setup_chat.py`·`docs/chat-setup.md`·`README.md` are fixed to the new ones.

## Things Not Included

| What | Why |
| --- | --- |
| Reconnecting to a running turn | A turn is a stream of one request. If you refresh the window, that turn is broken. Moving between worktrees does not break because the screen holds the stream. If needed, place an event buffer per session and the stream tails it |
| Codex read session to `app-server` | Step 5 deferred this here. The `exec` of the read session uses the isolation flag of easy explanation (`--ephemeral`, `--ignore-user-config`, tool off), and finding the same isolation in `app-server` has nothing to do with the screen. Do it separately |
| Installation file, Python bundling, code signing | Outside the scope agreed with the user |
| Coordinator/Review cell | Same as overview |
| `claude_session`·`codex_session`·`FINDERS` of `workspace.sessions` | Mirror was the only caller. Since that test is reconsidering the ownership judgment that also follows `checkouts`, deleting it is done separately, like moving the test |
| Leaving approval in work record | The record is up to the turn's tool bar. If you reopen it, what was allowed is only visible through the tool bar |

## Order

Commit unit. One PR.

1. `lint` views `tool/main/` as main. Red tests
2. `tool/main/` — Move queries, attach work/approval/switch/source check. `test_main.py`
3. Tauri shell — sidecar, window, terminal, execution command
4. Screen — `design-pass` 1~5
5. Deletion and documentation

## Screen Order

Five stages of `design-pass`. Values are in [`DESIGN.md`](../../../../DESIGN.md)].

| # | Done |
| --- | --- |
| 1 | Four areas from one line. Rewrote `DESIGN.md` from mirror's to this window's |
| 2 | Ends under two headers misaligned at 87/58px → both two lines, 87px. Folded at 960px to 147/128px → model selection label to screen read-only, reduced width. No horizontal scroll. Button height 27/28 → 28 one. Pressed everything that can be pressed — create, focus, model, → task, allow, open citation, map, translation, theme |
| 3 | Character combination 19 → 7 stages. Usages do not overlap |
| 4 | Reconsidered contrast of candidate set and asked user. Blue + approval only amber (`wait`) |
| 5 | No place to fill. Empty space on query side is where conversation grows, and there is an empty state sentence for each side |

## Verification

| Check | Result |
| --- | --- |
| `pytest tool/` | 296 passed. 25 mirror tests removed, six new tests in `test_main.py`, fourteen more in review round |
| What new tests look at | Source check, translation switch off, path outside worktree list, `session_id` of approval, duplicate citation in draft, each red if deleted. Pipe test red in pre-fix code with 20s limit |
| `python tool/lint.py --check` | Exit 0. Tests that planted calls outside `__all__` of `tool/main/` and `main` import of pipeline were green before fix |
| `test_lint`·`test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory`, `graph.py`, `ruff`, `npm run build` | Passed |
| `python tool/main --check` | Conversation continues even if effort is changed to actual Claude |
| Browser full process | Create worktree → wiki query (haiku) → open answer citation → "→ task" draft → agent (haiku) asks for write → allow → file created only in worktree, not in original → agent answer in Korean |
| Tauri window | Sidecar launches on empty port and window opens. PowerShell 7 opens in worktree in terminal, input/output/Korean characters exchanged. If window is closed, both server and shell go down. Even if app is forced killed, server goes down |
| Two things found in window | While sidecar was reading stdin pipe in thread, Windows `CreateProcess` blocked, stopping all paths calling `git`. In remote source, ACL blocked commands not in app manifest, so `pty_open` was rejected. Both fixed |
| Review round 1 | P1 set. Fixed after reproducing all — if project is changed while agent is running, the worktree of the waiting approval disappears from the list, answer 404 (blocks switching on both server and screen), if a name same as deleted worktree is created again, old conversation and CLI session attach (move record aside when deleting), stream broken before body starts holds busy forever (move holding to inside body). Two `일부` against plan — map does not follow translation switch (fixed), source check port (matched sentence to code). New test set and mutation net red each |
| Review round 2 | P1 two. As round 1 moved holding to inside body, project switch/cleanup intervened between receiving request and body start — hold at the moment of receiving, release at both `finally` of body and when body is collected (`weakref.finalize`). Marker's late release could not clear noise of next request. Screen filled old conversation into new task of same name because record lookup started before cleanup finished after cleanup — placed generation per path. Found one more while fixing: release at collection stopped waiting for lock held by same thread (mutation execution sometimes hung). Made lock reentrant |
| Review round 3 | P1 three, three faces of same family — acted after checking and releasing. Changed model: all requests changing worktree (`say`·`reset`·`remove`) hold that worktree, `say` holds before path check. Query and task use one lock — project switch reads all noise and changes under it. Nothing that will fail is placed before release after holding (`query.say` reads settings first). Fixed all three after reproducing, new test sets red if reverted each |
| Review round 4 | Three of round 3 resolved. New P1 set is widening model by one cell — whose is the project. Worktree creation also holds path to create, reads under same lock as project. Switch changes selection and `project.json` only after reading new project's settings. Screen's worktree list accepts only response of newest request (reproduce by delaying old response in browser — without guard, old worktree stood next to new project name) |
| Review round 5 | P1 two, remaining corners of round 4 fix. Switch reads settings by passing new project as name (`config(cid, name)`), and changes `_project` only after done — switch that will fail does not appear in list read without lock. Screen clears old list at the moment of switching (verify by failing new list in browser — empty list and error) |
| Review round 6 | P1 one. Server switch succeeded but if following `/api/channels` failed, screen was old project, server was new project. Screen follows project of switch response immediately, worktree list carries its own project name and if it differs from project visible on screen, discards and re-matches (verify by failing `/api/channels` in browser) |
| Review round 7 | P1 one. Even if list informed server's project change (switch of other window), kept old list and selection. Gathered path for screen to follow server's project into one `follow` — whether own switch or informed by list — clears old list and selection at that moment (verify by failing list following switch by other client in browser) |
| Review round 8 | P1 one. In continuous switch, late `/api/channels` of front switch reverted screen to old project. Since there are multiple places calling channel list (first load, switch, after query), judge by content instead of numbering each time — accepts only list of project screen expects (changes only `follow`). Hold B's response for 20s in browser and switch to C — C even after released |
| Review round 9 | P1 one pointed to root of this family — server's project selection is one but screen's request did not say which project it meant. Question sent before noticing switch of other window entered conversation of new project. All requests carry screen's project with `X-Project`, server rejects with 409 if it differs at one place reading project (`project()`) and informs where it changed to. Since holding requests read after holding or within same lock, check and work are not split by switch. Only switch itself (`/api/config`) is excluded. Screen `follow` if it receives rejection |
| Review round 10 | P1 one. Removed `/api/config/*` from check, but that path also handles focus's model change, so old screen's model change reverted server to that screen's project. Eliminated exception — switch of screen showing current project passes check, old screen does not even switch. Switched request changes its own claim to new project afterwards |
| Review round 11 | No new P0/P1, merge allowed. P2 one (query before first channel load has no claim) left as PR comment |