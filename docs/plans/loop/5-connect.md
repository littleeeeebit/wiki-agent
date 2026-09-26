# Step 5 — Connection and Full Investigation

The relationship between the overall design and the steps is in [Overview](0-overview.md)]. The investigation session is the specification of [Step 3](3-spec.md)], and the investigation PR runs in the loop of [Step 4](4-review.md)].

Goal. Attaching a repository to the wiki becomes a single [Connection] on the list screen. Whether it is attached is determined not by files, but by whether the actual session of the two hosts receives an injection. If turned on, [Connection] investigates the first wiki of that repository and uploads it as a PR.

## Agreements with Users

2026-09-25.

| What | Agreement |
| --- | --- |
| Repository attached to old hub | Do not move separately. The hook is per-user, so if the hub is changed, everyone calls the new hub. The adapter is used as is. Only repositories with remaining old project-level hooks are seen as `일부` and [Connection] is recommended. `ai-coding-agent-wiki-public` remains as a copy for distribution |
| Investigation switch | Default off |
| Investigation limit | Both tokens and time. Stops at whichever is reached first. Estimates are also provided in tokens and time |
| Investigation model | Claude opus. Changed in settings |
| Pages used by investigation | One structure overview in `.wiki/project.md`, module pages in `.wiki/modules/*.md`. Module pages are not injected because `triggers` is missing. Used only for search and map. Injection is only done for rule pages |
| Criteria for empty space | File-based. If a file with that name exists, do not touch it. README and `docs/` are read-only |
| SessionStart test | One actual CLI session turn. Per host |
| Codex hook trust | Included in the [Connection] confirmation window with the lines to be changed. If confirmed, trusted as `--trust-codex` |
| Unestimated slots | Becomes candidate material for the next work focus. Filled in the work tree and entered as a PR |

## Connection Status

`tool/main/connect.py`. Change the `wired: True` fixation (`tool/main/channels.py:100`) of `channels.projects` to this judgment. For the list, only cheap calculations are reported every time, and expensive tests read the record.

| Item | How to view | Cost |
| --- | --- | --- |
| adapter | `.wiki/adapter.toml` exists and required slots (`gate_cmd`) are filled. Empty selection slots are not `일부` but a row notification | One file |
| Per-user hook | There is a command in each host's user settings that calls this hub's `tool/hook.py` | Two files |
| Old project hook | Does not use `apply.unwire` and there is nothing to turn and change | Two files |
| Codex trust | `trustStatus` of `setup_agents.codex_hooks` | `app-server` once. Reads record in the list |
| Per-host test | Last test result and time of `raw/connect/<repo>.json` | Read record |

Status is the three in the overview. `일부` writes missing items by name — "Codex test failed", "Old hook remaining".

## SessionStart Test

Currently, `setup_agents.probe` runs by imitating `hook.py claude session_state.py` (`tool/setup_agents.py:212`). We do not know if the CLI actually calls that hook. As decided, run an actual session.

1. The server creates a one-time value (`nonce`) and launches the CLI with the environment variable `WIKI_PROBE=<nonce>` attached
   - Claude — `claude -p --model haiku "Reply OK"`, in the target repository
   - Codex — `codex exec --model <가장 싼 모델> -c model_reasoning_effort="low" "Reply OK"`, in the target repository
2. `hook.py` leaves `{host, event, injected, chars}` in `raw/connect/probe/<nonce>.json` if `WIKI_PROBE` exists. The hook is a child of the CLI and inherits the environment. When there is no value, it does nothing
3. When the CLI ends, read that file. If `injected` of `SessionStart` is true, that host passes
4. Leave the result in `probe` of `raw/connect/<repo>.json` with the time

Adding to `hook.py` is this one branch. Since the hook must remain open even if it fails (`craft/hooks-fail-open`), if the record cannot be written, it passes quietly. In that case, the test appears as a failure.

Tests run only at the end of [Connection] and [Re-test] in the list row. It costs two turns of the model at a time.

## Hub Migration

Once per machine. If a per-user hook calls a place other than this hub, the first [Connection] asks this first.

- Divide the part that returns the list to change `setup_agents.install_global` into the part that returns it and the part that uses it. Currently, `check` prints a string. The list is `[(파일, 바뀔 줄)]`
- Skill link. Add to the list to change every link pointing to the old hub's `skills/` in `~/.claude/skills` to the same name of this hub. Leave names not in this hub and notify. Links are made of the same type as the current ones
- If Codex trust is needed, include it in the same window
- The confirmation window shows all lines. [Confirm] writes, and then re-judges the status of all adapter repositories

## [Connection]

The order is exactly as in the overview. Only the concretized parts are written.

1. Hub migration (when needed)
2. Create adapter. One exception used by the server for original checkout

   | Slot | Estimation |
   | --- | --- |
   | `gate_cmd` | `pyproject.toml`·`setup.cfg`·`pytest.ini` → `python -m pytest`. `scripts.test` of `package.json` → `npm test`. `Cargo.toml` → `cargo test`. `go.mod` → `go test ./...`. If multiple, the top one. If none, empty value and connection is `일부` |
   | `review_dir`, `scratch_dirs` | `artifacts/review`, `artifacts/` |
   | `live_cmd`, `server_stop` | Do not estimate. Empty value |

   Leave the hash of the written content in `raw/connect/<repo>.json`
3. Remove old project-level hooks. `apply.unwire` and `keep_denies`
4. Per-host test

Empty slots enter the material for the next work focus of Step 3 as an "Empty adapter slot" section. When that specification fixes the adapter in the work tree and uploads it as a PR, the copy of the original after merge changes to the "Handover" procedure of the overview.

## Full Investigation

If turned on, it follows after 4 of [Connection].

### Estimation

| Measuring | How |
| --- | --- |
| Number of files and code size | `git ls-files` and size. Exclude products and lock files |
| Document volume | Bytes of `*.md` |
| History | Number of `git rev-list --count HEAD`, `gh pr list --state merged --limit 1000 --json number` |

The token estimate adds the quotient of the amount to be read divided by 4 to the amount written and tool calls. The coefficient is adjusted by the actual measurement of the first investigation. The time estimate divides tokens by the throughput measured per second on this machine. Both are shown and confirmed. If the limit in settings is smaller than the estimate, "Stops at limit — only part of module pages" is shown together.

### Investigation Session

One specification of Step 3. Created by the server.

- `id` `wiki-bootstrap`, `goal` "Initialize wiki for this repository"
- `done` — `gate_cmd` (if exists), hub's `lint --check`, `repo_lint`
- `out` — Fix existing files, fix code

After creating the work tree, the server writes the original's `adapter.toml` to the work tree and leaves it as the first commit. Then launch the investigation session.

To keep the limit, it must be measured between turns. Claude gives the token count when the turn ends. Therefore, the investigation is divided into one turn per step, not one turn.

| Turn | What is used |
| --- | --- |
| 1 | `.wiki/project.md` — Structure overview |
| 2…k | `.wiki/modules/<이름>.md` — A few modules each |
| k+1 | Rule page. If there is no SCHEMA format or evidence, do not use `landmine` |
| k+2 | Decision record. Run `harvest` from the beginning |
| k+3 | Correction of adapter slots |

Every time a turn ends, compare the tokens used and elapsed time with the limit. If reached, stop there and receive `done-report` for the amount used and upload a PR. The time limit is reached even during a turn — at that time, cut the turn with [Stop] of Step 2 and go with what was committed up to the previous turn.

When the PR is established, the loop of Step 4 runs.

### Invariants

Exactly the invariants of the overview. Investigation code does not have branches in `sync`, `harvest`, or retrospection. Tests pin this down — `sync.py`·`harvest.py`·`session_state.py` become red if they import `connect` or investigation settings.

## Handover

There are six steps to hand over the adapter copy of the original after the bootstrap PR is merged in the overview. The code is `connect.handover(repo, pr)`. Called after [Merge] of Step 4 merges the `wiki-bootstrap` specification.

- The order overlaps with the original ff-only of Step 4. Handover is first — if the adapter is not moved, it is blocked by the adapter that ff does not track. If handover succeeds, 5 inside it does ff. If it fails, the cleanup of Step 4 skips ff
- There is one place where handover ends while stopped — connection is "Partial — waiting for adapter reflection", and the original's adapter is in its original place (reversion) or the path is known. This is when the merge commit has not reached yet or `mergeCommit` is empty. A repository in this state calls handover again when reading the list (when the window receives focus). Up to once per minute. Handover starts from the beginning — from condition check 1, and if a copy of the previous attempt remains in `.git/wiki-connect/`, 2 stops (Review Round 10)
- The specification of [Merge] in Step 4 ends separately from handover. The specification becomes `머지됨` and the work tree and branch are cleaned up. What is needed for handover is only the PR number and original checkout, and the work tree is not used. If handover stops, 7 of [Merge] skips ff (above)
- Each of the six steps of the overview is one test. Check if nothing was touched for each violated condition, and if reversion is only when the original place is empty

## Screen

Temporary. Step 6 rebuilds it into the project list on the center face.

- [List] next to project selection on the rail changes the center face to a list. Status, missing items, [Connection], [Re-test], investigation progress for each row
- Confirmation window for hub migration. Lines to be changed per file
- Confirmation window for investigation estimate
- Three settings (investigation switch, token limit, time limit, investigation model) are `raw/chat/main.json`. Temporary input fields

## Test

| What | Case of becoming red |
| --- | --- |
| Status judgment. Repository missing one item each is `일부` and its name | If item checks are deleted one by one |
| Four slot estimations and failure to estimate | |
| Test. Pass if band CLI inherits environment and calls `hook.py`, fail if not | If file check is deleted |
| `hook.py` writes nothing without `WIKI_PROBE`, and hook succeeds even if writing fails | |
| List of hub migration appears fully before writing, and nothing changes without confirmation | |
| Investigation limit. If band session tokens exceed limit, do not send next turn | |
| Empty space judgment. Existing `project.md` remains as is | |
| Six steps of handover | If conditions of overview are violated one by one |
| `mergeCommit` does not ff in empty response and stops at "Partial — waiting for adapter reflection", and goes to the end when value comes in next list read | If empty value check is deleted |
| Invariant import check | |

## Things Not Done

| What | Why |
| --- | --- |
| Reconnect repositories of old hub all at once | User's decision. [Connection] per row |
| Module page injection | User's decision |
| Adding content to existing pages | Decided per file |
| `live_cmd` estimation | Meaning differs per repository. Human fills in as next work |

## Confirmation

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- Actually move this machine's hub. The lines in the confirmation window are the same as the lines actually changed. After moving, check if the status of `ai-generation`, `ai-nara-shop` is correct
- One [Connection] with investigation turned off is done by Step 7. Here, run [Connection] with investigation turned on to the end once with a discarded repository

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Status | `connect.status`, `channels.projects` replacement | Complete |
| 2 | Test | `WIKI_PROBE`, actual test per host | Complete |
| 3 | Hub migration | `install_global` division, skill link, confirmation window | Complete |
| 4 | [Connection] | adapter estimation and writing, hash, unwire | Complete |
| 5 | Investigation | Estimate, investigation specification, turn division, limit | Complete |
| 6 | Handover | `handover` and six tests | Complete |
| 7 | Screen | Temporary list, two confirmation windows, temporary settings | Complete |
| 8 | Gate | All of the above confirmation | In progress — auto confirmation passed. [Connection] with investigation turned on for discarded repository remaining |

## Decisions in Implementation

Decided while implementing parts not mentioned in the plan.

- Test records are appended one line per hook in `raw/connect/probe/<nonce>.jsonl`. One session calls multiple hooks, and a hook that does not inject (`keepalive.py`) must not overwrite an injected line
- The gate of investigation is `repo_lint --no-wiring`. The new work tree does not have `.claude/settings.json`, so all deny rules are caught as wiring drift. Wiring is seen from the original checkout
- "No uncommitted changes except adapter" in handover does not count output products (`.wiki/`, `corpus.json`, `graph.json`, `trajectory.jsonl`, `installed-agents.json`, `.gitignore`) used by the hook. In connected repositories, it occurs from the first session
- Every time an investigation turn ends, the server commits what remains, and reverts changes that touched outside `.wiki/` or existing files (except adapter) to commit. There are repositories that ignore `.wiki/*`, so `.wiki/**/*.md` is forcibly added
- Token limit counts new input/output/cache writes. Cache reading counts context again per call and consumes the limit
- After hub migration, delete test records of all repositories. Tests for old wiring are not evidence of the new hub
- Do not overwrite adapter where `[연결]` already exists. Repositories attached to the old hub only do unwire and test

Seen on this machine on 2026-09-25. Per-user hooks and skill links already pointed to wiki-agent, so there were no lines to move in the confirmation window. [Re-test] of `wiki-agent` confirmed SessionStart injection (4,439 characters) in both actual sessions of Claude and Codex, becoming `연결 완료`. `ai-generation`·`ai-nara-shop` are `일부` because tests have not been run yet, and what is missing is only the test of the two hosts and Codex trust confirmation
