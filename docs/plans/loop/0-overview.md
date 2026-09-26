# loop — A cycle of asking, delegating, reviewing, and returning to the wiki

[The wiki-agent plan ](../done/wiki-agent/0-overview.md) is set up in 7 steps. Ask the wiki, delegate work to agents in the worktree, and use the shell. This plan connects the gaps above it.

Step-by-step plans are written as sub-documents just before starting.

- [Step 1 — Cleaning up discrepancies ](1-cleanup.md)
- [Step 2 — Agent's deferred tasks ](2-agent.md)
- [Step 3 — Specification and handover ](3-spec.md)
- [Step 4 — Review cell and loop ](4-review.md)
- [Step 5 — Connection and full investigation ](5-connect.md)
- [Step 6 — Screen and map ](6-screen.md)
- [Step 7 — Verification ](7-verify.md)

What was decided with the user during the detailing of steps 2–7 is in the "Decided with user" section of each document. Changes from this overview were corrected in the main text.

## Why do this

Problem. The core flow is "When you don't know what to do next, ask the wiki, organize the next task through conversation, and hand it over to the work cell," but it is currently loose.

- "→ Work" only passes one question and one answer. Decisions changed in subsequent conversations are lost
- The to-do list is an empty line at the bottom of the draft. There are no completion conditions, and the work session lacks a system prompt
- There is no return to the wiki side after work is finished
- The review loop consists only of procedure documents and skills, and a human must type "review loop" every time
- To attach a repository to the wiki, one must enter that repository and request it in natural language
- The map only draws the rules of this wiki. The document graph (`repo_graph.py`) of the repository itself is created but not visible
- The screen grew by adding features. The map is just the old landing page attached as is

Solution. Connect from asking to merging as one cycle, and set the unit of that cycle as one work specification. The specification binds the worktree, session, PR, review round, and decision record together.

One-line goal. A person talks to the wiki to define the next task as a specification, clicks it to start work in the worktree, the PR goes through the review loop and is merged, and the result returns to the wiki.

## Decision

Confirmed with the user on 2026-09-25.

| What | Decision |
| --- | --- |
| Deferred | Cleaning up discrepancies, work turn reconnection, approval records and "allow during this session", Codex reading session before `app-server` and token count. Doing all four |
| Reviewer | Read-only review cell within the app. Does not use Orca terminal |
| Loop scope | Automatic until merge approval. Waits for a human only for write approval. Stops if the round limit is exceeded |
| Loop end | Stops and notifies "Merge possible". If a human clicks [Merge], it does `gh pr merge`. Only cleans up after the PR actually becomes `MERGED` — if it enters a merge queue or auto-merge and is still open, it leaves it as `머지 대기` and checks again. Remaining P2 is one PR comment |
| Loop button | One "Review loop (N)" on the rail. Turns on if there is an open PR. If one, starts immediately; if multiple, multiple selection in a modal. Tasks with attached PRs display `#번호` on the rail |
| Multiple PRs | Run simultaneously. Simultaneous execution count is limited in settings |
| PR without worktree | Receives the PR's head branch to create a worktree. Does not accept PRs from forks |
| Map | Follows the projects on the rail. The document graph of that repository is the default, with hub rules layered on top. Removes title phrases and number cards, leaving only one-line indicators |
| Organization method | Dedicated "Next task" focus. The agent proposes candidates first, asks back with options, and issues a specification |
| Specification | Goals and exclusions, completion conditions, evidence pages and files, decisions made in conversation |
| Handover | [Start] creates a worktree and sends the specification as a system prompt for the first turn |
| Returning | Status per specification, summary of results to the original conversation, decision record, `plan-active` update. The latter two are included in the PR. `plan-active` is with the merge, and the decision record goes into the next `sync` |
| Connection tab | Project list screen. Status and [Connect] for each repository |
| Hub | wiki-agent is the hub. User-level hook and `~/.claude/skills` links currently point to `ai-coding-agent-wiki-public`. Shows what will change during the first connection and asks for confirmation before moving |
| Installation method | Hook is once per user (`setup_agents --global`). [Connect] creates `.wiki/adapter.toml` in the repository, removes old project-level hooks, and verifies with SessionStart test per host. Both Claude and Codex |
| Full investigation | Switch in settings. If turned on, [Connect] also performs an investigation. Shows estimates, asks for confirmation, and stops at the limit in settings |
| Investigation output | Structure overview and module page, rule page for injection, decision record, adapter slot estimation |
| Investigation reflection | Uses `wiki-bootstrap` worktree and raises a PR. Subject to review loop |
| If documents exist | Fills only empty spaces |
| Screen | Work-centered 3-column. Settings is a gear modal. Query focus is on next task, wiki, and retrospective |
| Progress | Plan document first, one PR per step |

## One cycle

```
 다음 작업 초점 ─ 후보 3~5 ─ 되묻기 ─ 명세 카드
        ▲                                │ [시작]
        │                                ▼
  결과 요약 한 턴                 작업트리 + 작업 세션 (명세가 시스템 프롬프트)
  결정 기록 (harvest)                    │ PR
  plan-active (PR 안에서)                ▼
        ▲                          리뷰 셀 ⇄ 작업 셀   (라운드마다 gate · push)
        │                                │ 머지 허용
        └──────────── [머지] ◀── 머지 가능
```

The status of the specification is `정리됨 → 작업 중 → PR #n → 리뷰 대기 ⇄ 리뷰 Rn ⇄ 고치는 중 Rn → 머지 가능 → (머지 대기 →) 머지됨`.
`머지 대기` is when it enters a merge queue or auto-merge and the PR is still open, and cleanup is done only at `머지됨`. The status can exit to `멈춤` from anywhere, and a stop includes a reason. The list of status values is the table in [Step 3](3-spec.md), and the stop reasons and [Continue] per reason are all in the table in [Step 4](4-review.md). This document does not transcribe the list. Waiting for approval is not a stop but standing by, and is shown separately as `waiting` of the specification.

## Work specification

Specification is one JSON. Placed in `raw/specs/<repo>/<id>.json` of the hub. Since this is the operating state of the machine, it is not committed to the target repository. Only what is included in the PR goes to the target repository.

During merge, the server does not write to the target repository. The two things returning to the wiki are included in the PR before merging.

- Decision record. Writes `decisions` of the specification to the `## 변경 이유` section of the PR body. After merge, the existing `harvest` creates a decision record in that section (`tool/harvest.py:177`). It is the same reason why the wiki accumulates in the same path whether investigation is on or off. The record is created not at the moment of merge, but at the next `sync` of that repository.
  `sync` is the Stop hook and decisions are found at 6-hour intervals (`tool/sync.py:28`). If the server runs `harvest` immediately after merge, it would write to the original, so it does not run it. Therefore, `머지됨` of the specification does not wait for the record, and the specification card shows "Decision record: at next sync" and changes to a link when the record file for that PR number is created
- `plan-active` update. If the specification came from a row in the plan (the table in `docs/plans/` or `.wiki/plan-active.md`, that one row pointed to by `source` of the specification), after the PR is raised, the work cell adds a commit to the PR that changes the status column of that row to `완료 — PR #n`. The review cell also sees this commit. Specifications without a source row do not change any documents

| Column | What | Who fills it |
| --- | --- | --- |
| `goal` | One-line goal | Next task focus, human modifies |
| `out` | Exclusions | Same |
| `done` | Completion conditions. Adapter's `gate_cmd` and confirmation items unique to this task | Same. `gate_cmd` is always added by the server as the first item |
| `grounds` | Cited wiki pages and file paths, rules applied. List, not body | Extracted from conversation |
| `decisions` | Why this method, discarded alternatives | Extracted from conversation. Becomes `변경 이유` of the PR body, and `harvest` makes it a decision record after merge |
| `source` | Focus and turn of the conversation where the specification emerged, and the row of the plan document if any | Machine |
| `task`·`worktree`·`pr`·`rounds`·`state` | Position while the cycle is running | Machine |

Work sessions receive the specification as a system prompt. To say it is finished, one must write the results of running the items in `done` one by one. The server does not trust that report and runs `gate_cmd` one more time in the worktree before pushing and raising a PR. Currently, work sessions have no system prompt (`work.py:202`).

## Next task focus

Absorbs the work previously done by progress and diagnostic focus. Proposes candidates in the first turn. There are five sources for candidates.

- Remaining rows of `plan-active` and `docs/plans/`
- Open PRs and their review status
- Recent decision records
- Warnings from `lint --check`
- P2 remaining from review

When a candidate is selected, it asks back for scope, completion conditions, and exclusions as options. Options are drawn as buttons within the answer. Once everything is decided, it issues a specification card. The server checks the shape of the specification — an empty `goal` cannot become a card. `gate_cmd` is added by the server and cannot be omitted, and repositories without `gate_cmd` in the adapter are told to connect first. If one specification is split into multiple tasks, there are multiple cards.

## Review cell and loop

Review cell is a read-only session, one per worktree. The model is selected in settings, default is Codex. The round instructions and the shape of the results use `operator/codex-review-loop` as is. The review cell does not write results to a file but issues them as the final answer. The server leaves the instructions and results in `raw/review/<repo>/<pr>/` of the hub. If placed in `review_dir` of the worktree, the work cell can commit it, and it disappears when the worktree is deleted.

One round is as follows.

1. Server writes round instructions. Includes allow list, `git diff --shortstat`, and processing results of past findings
2. Review cell reads `gh pr diff` and issues `[P0|P1|P2] file:line` and the last line `머지 허용`·`머지 불가`
3. Server parses the results. If the last line is missing or the round number does not match, that round is a failure
4. If not mergeable, sends findings to the work cell as one turn. The work cell reproduces, fixes, and commits. If it disagrees, it provides evidence. Writing is approved as it is now. Server runs `gate_cmd` and if it passes, pushes
5. Next round

Loop ends with `머지 가능` when merge approval is issued, and otherwise stops only for reasons in the stop reason table (Step 4). The number of rounds must decrease. If findings do not decrease, insert `codex-review-loop` finding bundling into the instructions.

Loops live longer than HTTP responses. Therefore, the reconnection in step 2 must be set up first. Even if the window is refreshed, the loop is running, and the screen catches up from the event buffer. If the app is closed, the server goes down, so the loop also stops. If something is running, it asks for confirmation before closing, and if relaunched, [Continue] at `멈춤 — 서버 재시작`.

Open PR list is `gh pr list --json number,title,headRefName,headRefOid,headRepositoryOwner,url`.
Re-read when the window receives focus and when the loop status changes.

PRs without a worktree cannot be received with `workspace.create`. `create` creates a new branch from the original HEAD (`tool/workspace/worktrees.py:38`). Then the review cell sees code different from the PR. Therefore, add an entry point to receive existing branches in `workspace`. Fetch `origin/<headRefName>`, create a worktree tracking that branch, and verify if the worktree's HEAD is the same as the PR's `headRefOid`. If different, clear the created worktree and reject. Fork PRs where the head repository owner is different from the original cannot be listed and selected because there is nowhere to push.

## Connection and full investigation

Project list screen shows all folders with `.git` in the workspace (`channels.projects`).
There are three statuses per row.

| Status | Condition |
| --- | --- |
| Connection complete | Has `.wiki/adapter.toml` and slots are full. User-level hook calls this hub. Injected by SessionStart test of Claude and Codex respectively. Codex's wiki hook is trusted. No old project-level hooks |
| Partial | Any of the above is missing. Writes what is missing in the row |
| Unconnected | No adapter |

Currently `channels.projects` unconditionally writes this repository as `wired: True`. Change to this judgment.

What [Connect] does.

1. If the hub is not yet wiki-agent, shows the lines to be changed in user-level hook and skill links, and after confirmation, performs `setup_agents --global` and link replacement. Only once per machine
2. Creates `.wiki/adapter.toml`. Slots are estimated by looking at the repository — if `pyproject.toml`, then `pytest`, if `package.json`, then that `test` script. Unknown slots are left as "Must fill" in the row
3. Removes old project-level hooks (`setup_agents` of `unwire`)
4. SessionStart test per host. Currently `setup_agents.probe` simulates only `hook.py claude session_state.py` (`tool/setup_agents.py:213`). Codex only looks at the hook list and trust status. Therefore, run actual CLI sessions of both hosts one turn each, and connection is complete only if both leave traces that the hook called by that session injected

If full investigation is on, it continues after 4.

1. Estimate. Issues expected tokens and time based on file count, docs volume, commits, and merged PR count, and asks for confirmation
2. Creates `wiki-bootstrap` worktree, and moves `adapter.toml` written to the original to the first commit of that branch. Since the worktree is created from the original HEAD, uncommitted adapters do not follow. Then launches an investigation session. This is also one specification — goal is "Initialize wiki of this repository", completion conditions are `lint --check` and `repo_lint`
3. Writing is structure overview (`.wiki/project.md`) and module page (`.wiki/modules/`, does not inject), rule page in SCHEMA format, decision record running `harvest` from the beginning, and correction of estimated slots. If a file with that name already exists, it does not touch it
4. If it hits the token limit or time limit in settings, it raises a PR with what has been written up to that point

Invariant. The way the wiki accumulates after connection is the same whether investigation is on or off. `sync` Stop hook, `harvest`, and retrospective run the same way. Investigation only makes the first content different. Investigation code must not have branches in these paths.

Adapter is used by the server for original checkout instead of the person who clicked [Connect]. It is the same write as manually running `setup_agents`, and one of the two exceptions for writing to original checkout (the other is ff-only after [Merge]). The place it writes is that one file. Since it writes directly to the original, connection runs the moment it is clicked.

- [Connect] leaves the hash of the written content in `raw/connect/<repo>.json` of the hub. Whether the copy of the original has been changed by human hands since then is determined by this hash
- If investigation is on, the adapter also enters the `wiki-bootstrap` PR, and the investigation may correct slots, making it different from the original. The one in the PR that passed review is the standard
- If investigation is off, a human does the commit. Shows "adapter not committed" in the row

After the bootstrap PR is merged, the copy of the original is passed as follows. There are two principles. The inspected content and the cleared content must be the same file, and the cleared file remains until the update is confirmed.

1. Check conditions. Original checkout is on the PR's base branch. The upstream of that branch points to the remote of the repository where the PR was merged. No uncommitted changes except for the adapter. If even one is off, it touches nothing and leaves the connection as "Partial — waiting for adapter reflection" and writes the reason
2. If even one file remains in `.git/wiki-connect/`, the previous handover is not finished. Does not move newly, leaves as "Partial — copy of previous handover remains" and notifies the path. Only when empty, moves `.wiki/adapter.toml` of the original to `.git/wiki-connect/<시각>-adapter.toml` (`os.replace`).
   `os.replace` overwrites the destination, so it blocks with two layers: making the path different for each attempt, and not starting if there is a remaining copy. Since it is a move, not a copy, there is no gap where human-fixed content is deleted after inspection and only old content remains. It is inside `.git`, so it is not a work file
3. Compare the hash of the moved file with the hash left by [Connect]. If different, it is a copy touched by human hands. Revert and stop only when the original spot is empty. If the spot is full, do not revert and notify the path of the moved file. Either way, leave as "Partial" and show the difference between the two copies
4. Bring in with `git fetch <원격> <base>`, and see if the PR's merge commit (`gh pr view --json mergeCommit`) is an ancestor of the brought ref. Otherwise, the update has not reached yet — revert and stop as in 3. If `mergeCommit` is empty, it does not yet know the merge commit, and counts as the same case. Does not go to 5 without knowing if it is an ancestor
5. `git merge --ff-only <원격>/<base>`. Since git does not overwrite untracked files, stop here if a human created a new adapter in the meantime
6. Verify. HEAD includes the merge commit, and `.wiki/adapter.toml` is tracked and same as the PR's. If all match, delete the file in `.git/wiki-connect/` and return to connection complete. If 5 or 6 fails, revert as in 3 and leave as "Partial"

## Map per repository

`/api/graph` receives `repo`. Creates the document graph of that repository in memory as `repo_graph.build`. `repo_graph.write` writes to `.wiki/graph.json` of the original, so the server does not call it (`tool/repo_graph.py:128`). That file is updated by `sync` Stop hook as it is now.

Layer the hub rules of `graph.py` that apply to that repository on top. Layers are toggles in the header — this repository document, hub rules, both.

Map follows the app theme. Places a one-line indicator (page count, orphan documents, lint warnings) under the header line, and the rest is filled by the graph. Selecting a node opens a side panel.

## Screen

```
┌───────────┬──────────────────┬────────────────────┐
│ ▾ 프로젝트 │ 위키  [대화|지도]  │ fix-login   #12 R2  │
│ ⚙ 리뷰(2) │ 다음작업·위키·회고 │ 명세 ▸ 목표·완료조건 │
├───────────┤                  ├────────────────────┤
│ 작업      │  후보 → 되묻기    │ [에이전트|리뷰|터미널]│
│ ● fix-log │  → 명세 카드       │                    │
│   #12 R2  │     [시작 ▸]      │  도구 줄 · 승인 카드  │
│ ◐ add-api │                  │                    │
│   승인대기 │ [묻기]            │ [지시]               │
└───────────┴──────────────────┴────────────────────┘
```

- The unit of the rail changes from worktree to work (specification). Worktrees created without a specification also become rows
- Remove the "New work" input field. Work is created by [Start] of a specification or the review loop. If an empty worktree is needed, create it as a one-line specification in the next task focus
- Translation/theme switches go to the settings modal. Modal sections are General, Connection, Review
- The right side is not divided into agent above and terminal below, but placed as tabs. If waiting for approval, a `wait` dot is attached to the agent tab
- There are three focuses. Diagnostics are merged into the wiki, and reviews go to the review cell

Problem of work order. The order chosen by the user is specification, review, connection before the screen. If followed as is, new features will be added to the old frame. Therefore, steps 3–5 build the server, contract, and tests, and attach to the screen only as much as needed for verification. Step 6 rebuilds the entire screen in `design-pass` order. At this time, the temporary screens of steps 3–5 are not left behind.

## Safety boundary

The boundaries of [wiki-agent overview](../done/wiki-agent/0-overview.md#safety-boundary) remain the same. Adding.

- Review cell is read-only. Sees the same worktree as the work cell but does not write. Blocked by execution permission, not prompt — Codex is read-only sandbox, Claude is `Read,Glob,Grep` without `Bash`
- Turns sent by the loop to the work cell are also approved for every write, just like turns sent by a human. "Allow during this session" applies only to that one session, that one tool
- Merging is done only with the [Merge] button. The loop does not merge itself. Merge is bound to the head commit that the review issued `머지 허용`. After merge, the remote branch is deleted only when it remains at that commit
- Server runs `gate_cmd` in the worktree. Same as the command the agent already ran and is not original checkout
- User-level settings and skill links are changed only after showing the lines to be changed and receiving confirmation
- Writing for full investigation is done only within the `wiki-bootstrap` worktree
- Server writes to original checkout only in two cases. `.wiki/adapter.toml` of [Connect], and `git merge --ff-only` of cleanup after [Merge]. The latter is done only when the original is on the base branch and there are no uncommitted changes. Map also does not write to the original. Also write the two exceptions in the boundary of `docs/plans/done/wiki-agent/0-overview.md`. Hooks running inside the host of the target repository (`corpus.json`·`graph.json` of `sync`) write to that repository as now — not a write by the server

### Where the screen writes to the server

Answers the three questions of `craft/screen-ownership-before-wiring` in advance.

| Screen | Which account runs | Judgment of late events | What can be written |
| --- | --- | --- | --- |
| Specification [Start] | Same as work session | Specification `id` and worktree path | New worktree, specification file |
| Completion judgment and PR | Server, human's `gh` | Specification `id` and head commit of worktree | `gate_cmd` in worktree, push, create PR |
| Review loop | Review cell is read, work cell is work session | Specification `id`, PR number, round number | Round file in hub `raw/review/`, write through work cell, gate and push |
| [Merge] | Human's `gh` | PR number and head commit. After matching the head commit allowed by review with what the screen saw, pass to `gh pr merge --match-head-commit` to make GitHub reject atomically. If read and compared in advance, cannot block push in between, and if only passing the commit seen by the screen, cannot block push after approval | Merge, cleanup after merge, ff-only of clean original, deletion of remote branch staying at allowed commit |
| [Connect] | Server | Repository path | `.wiki/adapter.toml` of that repository, confirmed user-level settings |

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Cleaning up discrepancies | Plan status judgment bug, old documents and comments, unused exports, small deferred tasks | Complete |
| 2 | Agent's deferred tasks | Turn reconnection (event buffer per session), approval records and "allow during this session", Codex reading session before `app-server` and token count | Complete |
| 3 | Specification and handover | Specification contract and storage, next task focus, [Start], system prompt of work session, returning result summary, `변경 이유` of PR body, `plan-active` update in PR | Complete |
| 4 | Review cell and loop | Review cell, round parser, loop state machine, concurrent execution limit, PR list, receiving existing PR branch, [Merge] | In progress |
| 5 | Connection and full investigation | Connection status judgment, SessionStart test per host, hub migration, [Connect], slot estimation, estimate and limit, investigation session | In progress |
| 6 | Screen and map | Work-centered 3-column with `design-pass`, settings modal, project list, review modal, map per repository | In progress — auto-test passed, window verification remaining |
| 7 | Verification | Two cycles in the window — new repository with investigation on and repository with it off | Not started |

Reason why step 2 is before steps 3 and 4. Work started with a specification and the review loop are long. If an automatic loop is placed on top of a turn that dies when the window is refreshed, the loop is tied to the lifespan of the window.

## Things not included in this plan

- Coordinator and worker. Splitting a specification into multiple is solved by multiple worktrees, and one session does not launch workers
- Back-reflection to public copies. Judged each time by the procedure of `docs/publishing.md`
- Installer, bundled Python, code signing
- Keeping text inside quotes in translation. Left as decided in step 7
- Quality standard for easy explanation (`docs/quality.md`). Re-examine after reducing focus to three
