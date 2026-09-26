# wiki-agent — A coding agent program centered on wiki queries

This repository is a private copy that inherits the history of `ai-coding-agent-wiki-public` as is. The purpose changes from here. It is no longer an accessory that attaches a wiki to Orca, but a single program that makes querying the wiki its main function and layers a coding agent on top of it.

Step-by-step plans are written as sub-documents just before starting.

- [Step 2 — Boundary check](2-boundary.md)
- [Step 3 — `translate` independence](3-translate.md)
- [Step 4 — `wiki` independence](4-wiki.md)
- [Step 5 — `agent`·`workspace`](5-agent-workspace.md)
- [Step 6 — Main and new screen](6-main.md)
- [Step 7 — Verification](7-verify.md)

## Why do this

Problem. Currently, the agent runs in Orca's terminal shell, and Korean is read separately in the wiki app's mirror tab or a second shell. One has to switch between two screens, and the mirror is structured to read session logs by tailing them, so it disconnects whenever the log path, `CODEX_HOME`, or worktree changes.

Solution. One program holds both the side that launches the agent and the side that displays the translation. Then, a Korean overlay can be placed right where the agent output appears, and there is no need to trace logs backward. On days when you don't want to spend money on translation, you can turn off translation mode — if turned off, the request itself is not sent.

One-line purpose. A person queries the wiki first, tasks the agent based on that answer, and reads the entire process in Korean on one screen.

## What is brought from Orca

This program completely replaces Orca. Since there is no period of using Orca together, Orca-specific code is deleted once the replacement screen is ready. Confirmed with the user on 2026-09-24.

| Orca's method | Here |
| --- | --- |
| Agent is not recreated, existing CLI is launched | Same. Both Claude Code and Codex. `ChatSession` already wraps both CLIs into one conversation shape |
| One worktree per task | Same. Where the agent writes files is always an isolated worktree |
| Worktree list and status | Same. List on the left of the screen |
| Multiple sessions running simultaneously | Same. One session per worktree |
| Place task cell and review cell side by side | Later. In this plan, review is done as a `review-loop` skill |
| Task distribution between agents | Separate plan. Here, only a placeholder is left for events — below |
| Screen is multiple terminals | Changed. Main screen is wiki query, agent session and terminal are attached next to it |

Agent and terminal are different things.

- The agent runs on an event stream (`stream-json`). The Korean overlay is placed on top of this event. The method of running the CLI inside a terminal and reading logs by tailing them is not used because it revives the current mirror's problems
- The terminal is a shell used by a person in the same worktree. It is drawn with xterm.js, and the pty is held by the Tauri host's Rust (`portable-pty`). The Python side does not know about the terminal

## Orthogonality — Pipelines do not know each other

Rule. One main function is one pipeline. Pipelines do not import or call other pipelines. Only the main does the work of weaving two or more together.

When violated. If translation is slow, the wiki answer is slow, and if the agent dies, the translation cache breaks, causing failure in one place to spread to the side. The current code has already caused accidents of that shape twice — utterance translation and page translation shared one deadline, so the English version starved (#18), and the overlay of long answers was cut off by the 6-second limit for hooks (#19). Both are things that happened because different functions shared one budget.

### Pipeline list and contract

Each pipeline is one folder under `tool/`. The folder is a package. The public entry point is `__all__` of `__init__.py`. After step 5, all four pipelines have `__all__`.

| Pipeline | Folder | Task | Input | Output | Code to move |
| --- | --- | --- | --- | --- | --- |
| `wiki` | `tool/wiki/` | Rule matching, wiki query answer, knowledge graph | Question, target repository | Answer event, citation page, matched rule | Matching part of `inject.py`, `wikilib.py` |
| `translate` | `tool/translate/` | Translation. If it fails, return the original text | List of sentences, direction, deadline | List of sentences of the same length | `translate.py` |
| `agent` | `tool/agent/` | Launch CLI agent in worktree and stream events | Instruction, worktree, model/effort | `delta`·`tool`·`approval`·`done`·`error` event | `chat_session.py`, `chat_local.py` |
| `workspace` | `tool/workspace/` | Worktree creation/cleanup, find session log | Repository, task name | Worktree path | `sessions.py` |
| Main | `tool/` root, from step 6 `tool/main/` | Weaves the above. Translation on/off, budget, screen | Human input | Screen | `chat.py`·hook entry point. Rewritten in step 6 |

Contracts are exchanged only as data. One pipeline must not know the output shape of another pipeline, and that conversion is done by the main. Budgets are also held separately for each pipeline — the structure of sharing one deadline was the cause of #18 and #19.

Agent events have `session_id` and `parent_id` from the beginning. The structure where the coordinator launches workers is built in a separate plan, but it is because if the event shape is changed later, the screen and overlay must all be fixed.

### There are two mains

Hooks run separately inside the host CLI. So there are two mains — the program's main, and the hook entry point inside the host (`inject.py`·`session_state.py`, etc., called by `hook.py`). Both only weave pipelines and do not enter into the pipelines.

Hook entry points are left as is in the `tool/` root. Because user-level hook settings and old project-level installations call `tool/hook.py`·`tool/inject.py` paths by name, if these files move, the hooks of already installed machines will silently break.

`tool/main/` is created when writing the new main in step 6. Just moving the current `chat.py` there only changes the paths of `chat.cmd`·`setup_chat.py`·documents and nothing is gained. It is rewritten and deleted in step 6 anyway.

### Places that were misaligned before step 2

This is the result of counting imports. It became 0 in step 2 (#3). `mirror.py` and `chat.py` are on the main side, so they are not subject to inspection, and are deleted in step 6.

| Where | What calls what | How to solve |
| --- | --- | --- |
| `inject.py` | `wiki` calls `translate` | Matching/rendering goes to `tool/wiki/`. `inject.py` remains as a hook entry point to weave matching and translation |
| `mirror.py` | Log reading calls `translate` | Remains on the main side (`tool/` root). Deleted when step 6 overlay is ready |
| `chat.py` | One server imports channel, mirror, translation, session state, Slack | Remains on the main side. Slack is deleted. In step 6, the new main is written to call only the public entry points of the pipelines |

### Rules as checks

As per the principles of this wiki, orthogonality is not left as a sentence but made into a check. `lint --check` looks at imports inside pipeline folders.

- Same pipeline, standard library, installed packages are allowed
- `tool/common/` is allowed. If something that two pipelines actually use together arises, create it then. `common/` cannot be imported by any pipeline. It was first created in step 5 as a worktree location (`worktree_home`)
- Other pipelines are not allowed
- `tool/` root module is also not allowed. Because if the root module calls another pipeline, it goes around the boundary that way
- Different spellings of the same module are also checked together. `tool.translate`, and relative imports going out of the folder (`from .. import translate`). Review round 1 passed the check through these two paths
- Delayed imports inside functions are also counted

- `tool/` root module uses only names inside the pipeline's `__all__`. It only looks at pipelines that have `__all__` (`lint.pipeline_surface`, discovery `공개 진입점`). Tests can look inside. `import tool.translate` binds `tool`, so it also looks at `tool.translate.x` (a hole postponed in PR #4, blocked in step 4)

Whether the check actually turns red is confirmed by `test_lint.py`. Implementation is `lint.pipeline_imports`, `lint.reached`, `lint.pipeline_surface`.

## Frontend

The app is wrapped in Tauri. The current Python code (FastAPI, translation, wiki matching) is used as is as a sidecar process, and the screen is drawn with React as it is now. The backend is not rewritten in Rust.

Screen order follows `craft/screen-follows-the-purpose` — extract the screen list from the one-line purpose, measure alignment, user chooses typo and color, margins last.

```
┌──────────┬──────────────┬──────────────┐
│ 작업트리 │  위키 질의   │ 에이전트 세션│
│ ● a      │  Q: ...      │ [한국어 오버]│
│ ○ b      │  A: ...      │              │
│ ○ c      │  근거 f:12   ├──────────────┤
│          │  [→ 작업]    │ $ 터미널     │
└──────────┴──────────────┴──────────────┘
```

| Area | What | What exists now |
| --- | --- | --- |
| Worktree list | Branch, change status, session connection status. Suggest cleanup for merged ones | None |
| Wiki query (main) | Ask, see answer and evidence `file:line` | `#위키` channel, `Answer`·`Peek` |
| Agent session | Korean overlay on top of agent output. Tool calls/patches are not translated. Write approval is also done here | `Mirror`, `useOverlay` |
| Terminal | Shell of the selected worktree | None |
| Wiki map | Rule graph. Separate screen | `WikiMap` |

When passing from a wiki answer to a task, create a draft. A person modifies and sends the instruction draft containing the question, answer summary, citation `file:line`, and matched rules. Currently expanding `Handoff`.

Theme is dark by default, and light is also provided. Palette is chosen by the user in the color step of screen order.

Translation toggle is a switch in the main, not the screen. It is one for the entire app and remembers the last value. If turned off, the app screen's translation does not send requests to `translate`. The English version that the hook puts into the agent is set separately from this switch — it is agent input, so it must not be cut off due to screen circumstances.

Agent session is a screen that writes to the server. Before building, write down three things of `craft/screen-ownership-before-wiring` — which CLI account it runs as, what determines if a late event belongs to this session, what is allowed to be written.

## Translation budget

`translate` measures its budget in two ways.

- Deadline per caller (seconds). The caller passes it. There is no default value. Hooks short, overlays long — so #19 does not happen again
- Monthly cost limit ($). Multiply the number of tokens in the response by the Gemini rate and count. If exceeded, return the original text for the remainder of the month. The limit is `TRANSLATE_MONTHLY_USD` of `.env`, default $5. Usage display on the screen is done by `usage()` in step 6

## Safety boundary

Currently, web chat is bound by `READ_TOOLS = "Bash,Read,Glob,Grep"`. The reason for fixing files in the browser is that it is a remote shell. The agent pipeline needs writing, so this boundary is redrawn.

- Server runs only on `127.0.0.1`
- Writing only inside the worktree created by `workspace`. Do not write to the original checkout — two exceptions. If you click [Connect], the server writes one `.wiki/adapter.toml` of that repository to the original ([loop overview](../../loop/0-overview.md#connection-and-full-investigation)). In cleanup after [Merge], only `git merge --ff-only` when the original is on the base branch and there are no uncommitted changes ([loop step 4](../../loop/4-review.md#merge))
- Worktree is created in `../<repo>-worktrees/<task>` next to the repository
- All writing is approved on the screen — file editing, shell commands with writing, even `gh pr create`. Claude Code is received as `--permission-prompt-tool stdio`, Codex as `app-server`'s approval request. Writing outside the worktree is refused without asking ([step 5](5-agent-workspace.md))
- The target repository's `permissions.deny` and wiki hooks are attached as is — because it is launching a CLI

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Direction confirmation | Decisions above. Tauri + Python sidecar, complete Orca replacement, agent is event stream | Done |
| 2 | Boundary check | Four pipeline folders, check for forbidden imports between folders and tests where that check turns red. Resolved 3 misaligned places, deleted Slack | Done |
| 3 | `translate` independence | One public entry point, monthly cost limit and cache. Deadline per caller is passed by the caller | Done |
| 4 | `wiki` independence | Run query/graph without translation. Answer event contract | Done |
| 5 | `agent`·`workspace` | Worktree creation/cleanup, writable CLI session, approval event, event contract. Apply safety boundary | Done |
| 6 | Main and new screen | Tauri window, `tool/main/`, three areas + terminal, overlay, translation toggle. Screen order step 5. When finished, delete `mirror.py`·old `chat.py` | Done |
| 7 | Verification | `pytest tool/`, `lint --check`, web build, entire process once with translation off, once with it on | Done |

One PR per step.

Reason why step 2 is first. If steps 3-5 are done without boundary checks, a person must visually confirm whether the boundary is kept every time each step ends. If the check is in place first, it turns red the moment it misaligns.

## Things not included in this plan

- Coordinator and worker. The structure where one session splits tasks and distributes them to multiple workers is planned separately. Since it was decided to get approval for all writing, the problem of approval requests from multiple workers flocking at that time is solved together
- Review cell. Read-only review session next to the work session — done in [loop step 4](../../loop/0-overview.md#review-cell-and-loop)
- Back-propagation to the public copy. Whether to return the changes of this repository to `ai-coding-agent-wiki-public` is judged each time by the procedure of `docs/publishing.md`
