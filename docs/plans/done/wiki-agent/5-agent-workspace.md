# Step 5 — `agent`·`workspace`

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. The agent becomes capable of writing, and that writing only occurs for items approved one by one by a human within a worktree created by `workspace`. Both pipelines have `__all__`, and checks like `translate`·`wiki` are attached. There is no screen — Step 6 builds upon this contract.

## Approval is already asked by the CLI

Both CLIs have a channel to ask before writing. The newly built part simply flows that question as an event and returns the answer. Both were actually run and verified in a disposable repository on 2026-09-24.

| CLI | How to launch | What it asks | How to answer |
| --- | --- | --- | --- |
| Claude Code 2.1 | `claude -p` stream-json with `--permission-prompt-tool stdio` | `control_request` of stdout (`subtype: can_use_tool`) | `control_response` of stdin — `updatedInput` to `allow`, or `message` to `deny` |
| Codex 0.156 | `codex app-server`, `sandbox: read-only` to `thread/start`, `approvalPolicy: untrusted` | Server request `item/commandExecution/requestApproval`, `item/fileChange/requestApproval` | `{"decision": "accept"}` or `"decline"` with that id |

Verified. In Claude, when `Write` was allowed, the file was created, and when `Bash` was rejected, it was not. In Codex, when rejected, neither was created, and when approved, both were created. Codex on Windows also writes files via PowerShell commands, coming in as `commandExecution` approval.

Only the Codex write session is launched with `app-server`. `codex exec` cannot receive approval. The read session (channel of `chat.py`, `explain`) is left as the current `exec`. Merging the two into one is the task of Step 6, which deletes the old `chat.py`.

## `agent`

### Public Entry Points

| Name | What | Caller |
| --- | --- | --- |
| `ChatSession(repo, ..., write=False, parent_id=None)` | One CLI per conversation. `say(text)` flows events | `chat` |
| `ChatSession.answer(approval_id, allow)` | Answers approval events | Step 6 main |
| `Event` | One event — contract below | `chat` |
| `explain` | Isolation session that rewrites answers in simple language | `chat` |
| `CodexServer`, `cli_command`, `settings`, `ROOT`, `SETTINGS` | CLI discovery and local configuration | `chat_channels`, `setup_chat`, `setup_agents` |

### Event Contract

```python
Event(kind, text="", meta={}, session_id="", parent_id=None)
```

| `kind` | `text` | `meta` |
| --- | --- | --- |
| `delta` | Answer fragment | — |
| `tool` | One-line tool summary | — |
| `approval` | One line of what it intends to do | `id` (used when answering), `tool`, `input` |
| `done` | Final answer | `ms`, `error`, `session_id` (CLI's continuation id), `model`, `tokens`, `cost_usd` |
| `error` | Reason for human to read | — |

`session_id` is the session id of this program. It is set when `ChatSession` creates it and does not change until it ends. This determines which session a late event belongs to. The CLI's id can only be known once the first response arrives and changes if the model is switched, so it cannot be used for determination. It remains in `done.meta` as it is now. `parent_id` is passed by the caller. It is the slot to be used when the coordinator launches a worker, and currently it is `None`.

### Write Session

Only when `write=True` is true is the write tool attached and the approval channel opened. The default is the same read session as now.

- Does not create if it is not a worktree created by `workspace` (`ValueError`). The parent folder of the worktree must be the `worktree_home` of that repository. Since a worktree created by Orca or `claude -w` is the same worktree in git, it writes to others' work if it only checks if it is a connected worktree (Review Round 1). The original checkout cannot be inside its own `-worktrees` folder, so it is caught by the same check
- Claude. Tools are `Bash,Read,Glob,Grep,Edit,Write`, non-asking tools (`--allowedTools`) are `Read,Glob,Grep` only. Specify `--permission-mode default` to prevent `acceptEdits` etc. in settings from skipping approval
- Codex. `read-only` sandbox and `untrusted` approval. Asks for everything except known read commands
- Writing outside the worktree is rejected without asking the human. Looks at the path of Claude's `Edit`·`Write`·`NotebookEdit`, and the `fileChange` path and command's `cwd` of Codex. Does not read paths inside shell commands — the human sees that in the approval event
- Turn deadline (600 seconds) does not run while waiting for approval. The turn must not die because the human is away
- The answer goes only to the process that asked. If the turn is discarded while the answer is late and the next turn launches a new process, the "allow" sent to the currently running process attaches to the new process's request. Codex counts request numbers anew for each process (Review Round 1)

### Which account does it run with

Two of the three in `craft/screen-ownership-before-wiring` are above — late events are determined by `session_id`, and what can be written is determined by the worktree. The remaining one is the account.

Sessions run with the login of the environment that launched this program. Claude is that user's Claude Code login, and Codex is that if `CODEX_HOME` exists, otherwise `~/.codex`'s login. When creating `ChatSession`, it captures the entire environment and passes it every time it launches a process. Therefore, restarts or `--resume` continue with the same account. Initially, it inherited the environment at launch as is, so if the server's `CODEX_HOME` changed in the meantime, the same session could continue with a different account (Review Round 2). A screen to select an account is not in this plan. The method where Orca kept `CODEX_HOME` separately for each account is not brought over

### What is not blocked

Since `permissions.allow` rules in the user or target repository settings are applied by the CLI first, writes caught by those rules are not asked about. Since it is an allowance written directly by a human, it is left as is. `permissions.deny` and wiki hooks are also left attached for the same reason.

## `workspace`

### Public Entry Points

| Group | Name | Caller |
| --- | --- | --- |
| Worktree | `create`, `worktrees`, `remove` | Step 6 main |
| Session Log | `SESSIONS`, `INJECTED`, `MAX_HUMAN_CHARS`, `parse`, `checkout`, `checkouts`, `folder`, `logs`, `FINDERS` | `census`, `transcript`, `hook`, `mirror`, `setup_agents` |

### Worktree

| Function | What it does |
| --- | --- |
| `create(repo, task)` | `git worktree add` with branch `<task>` in `../<repo>-worktrees/<task>`. Returns the path |
| `worktrees(repo)` | Worktree under that folder. `path`, `branch`, `dirty`, `merged` per line |
| `remove(repo, path)` | `git worktree remove`, and branch deletion |

- `repo` must be the original checkout. If you create a worktree inside a worktree, the folder ends up in the wrong place
- `task` is lowercase, numbers, and `-` only, up to 64 characters. Serves as both branch name and folder name, and Korean paths have been corrupted to `cp949` on this machine before
- The folder location (`worktree_home`) is in `tool/common/`. `workspace` creates it there and `agent` opens a write session only there. This is the first example of "two pipelines actually using it together" mentioned in the overview
- `merged` means "you don't lose anything that isn't in the HEAD of the original checkout if you delete it". It is true if all paths changed from the merge base to the branch are identical to the branch's version in the current HEAD (same blob, same mode). It reads the three trees (base, branch, HEAD) with `git ls-tree -r -z` and compares the mode/object per path here. It only receives the list from git and does not ask what changed. Squash merge, normal merge, and branches without self-changes are true. The screen sees this and suggests cleanup
- The previous six determinations each deleted a unique commit. All six let git decide what changed, and git's judgment had settings and rules involved in each

  | Determination | Case of error | Round |
  | --- | --- | --- |
  | Remote branch deleted (`gone`) | Can delete remote only without merge | 1 |
  | Same patch with `git cherry` | Found in history even if HEAD reverts later | 2 |
  | Apply diff to HEAD in reverse (`git apply -R`) | If the same line is in a different place in the file, it matches there | 3 |
  | Tree merged into HEAD (`merge-tree`), conflict exit code discarded | Modify/delete conflict leaves HEAD's version, so tree is same as HEAD | 4 |
  | Same thing, also look at exit code | Merge driver like `merge=ours` of `.gitattributes` clears the branch side | 5 |
  | Path-by-path comparison with porcelain `git diff` | `diff.ignoreSubmodules=all` hides submodule pointers that only the branch has | 6 |
  | Same thing with plumbing `diff-tree` | `ignore = all`, `submodule.<name>.ignore` of `.gitmodules` also follow plumbing | 7 |

- In Round 3, the conflict exit code check was deleted. It was seen as clutter because the test was green even if deleted. Green meant there was no test looking at that check, not that it wasn't needed (Round 4)
- In Round 6, it was moved to `diff-tree` relying on "plumbing does not read `diff.*` settings", but the submodule ignore setting was also followed by plumbing (Round 7). Settings determine what the diff family counts as changed. `ls-tree` is a list, so there is nothing to filter out
- Renaming also does not happen here. Since it looks at each path separately, `a→b` is two paths where `a` disappeared and `b` was created
- If not sure, it is false. HEAD not yet pulled, or HEAD after merge that re-modified a file modified by the branch, leaves the branch. There are more cases of leaving than merge simulation. The remaining branch is one line in the list, but a false true deletes work
- `remove` does not delete a dirty worktree. It deletes with `-D` only when the branch is `merged`, and leaves the rest. It does not accept paths outside that folder

## Check

`lint.pipeline_surface` looks at the pipeline with `__all__`. It is attached just by using `__all__` in both `__init__.py`. The root module uses `from agent import` instead of `from agent.chat_session import`.

## What was not included

| What | Why |
| --- | --- |
| Codex read session as `app-server` | There is no reason to change what is currently running for writing. Step 6 cleans it up with the old `chat.py` |
| Token count of Codex write session | It is in `thread/tokenUsage/updated`. Attached when the screen draws usage |
| "Allow for this session" in approval | Decided to get approval for all writes. If needed, there are `acceptForSession` and Claude's `permission_suggestions` |
| Coordinator and worker | Separate plan. Only leave `parent_id` slot in event |

## Verification

| Check | Result |
| --- | --- |
| `pytest tool/` | 304 passed. 285 before, 19 new tests (`test_agent` 6, `test_worktrees` 13) |
| What `test_agent.py` sees | If you delete the waiting turn deadline, delete rejection outside worktree, delete rejection of others' worktree, or send to current process instead of the process that asked, each is red. Approval is sent from a different thread later than both deadlines like the screen |
| What `test_worktrees.py` sees | Plants the case where the previous seven determinations are wrong, renaming, and mode-only changes one by one. If you delete HEAD comparison, ten are red; if you don't count deleted paths, two; if you don't look at mode, one; if `remove` ignores `merged`, two are red |
| `python tool/lint.py --check` | Exit 0. If you plant `from workspace.sessions import codex_homes`, `workspace.home`, `agent.READ_TOOLS` in `census.py`, `공개 진입점` three |
| `python tool/test_lint.py`, `ruff check tool` | Passed |
| Direct execution script | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` exit 0. `chat.py --check` passed — read session runs as is |
| Actual Claude (haiku) | Asked to write two files in a worktree created with `create`. Two approval events, one allowed/one rejected → only allowed file created. Second turn continued in the same process. When asked to write to a path outside the worktree, it rejected without asking and no file exists |
| Actual Codex (`app-server`) | Same instruction. `fileChange` approval came with path, and only allowed file created. Second turn continued |
| Cleanup | `remove` rejected two dirty worktrees. After clearing, worktree and branch were deleted |
| Review Round 1 | One P0 (`-D` with `gone` — unmerged commit loss), two P1 (write session in others' worktree, late answer to new process). Fixed after reproducing all three. Re-ran write/approval/cleanup with actual Claude |
| Review Round 2 | Checklist against plan — all items done or for next step, only two partial. One P0 (`git cherry` also sees reverted patch as merge), one P1 (description that account is fixed at session creation differs from code). Fixed after reproducing both |
| Review Round 3 | One P0 (`git apply -R` matches in different place of same block). Third time for same function, so changed determination to three-way merge tree comparison |
| Review Round 4 | One P0 (merged tree same as HEAD in modify/delete conflict). Revived conflict exit code check deleted in Round 3 |
| Review Round 5 | One P0 (merge where `merge=ours` driver discarded branch changes is same as HEAD). Fifth time for same function, so discarded merge simulation and changed to path-by-path object comparison |
| Review Round 6 | One P0 (`diff.ignoreSubmodules=all` hides submodule pointers). Changed porcelain `git diff` to plumbing `diff-tree` |
| Review Round 7 | One P0 (`diff-tree` also follows `ignore = all` of `.gitmodules`). Discarded diff and read three trees with `ls-tree` to compare. Added mode-only change case as test |
| Review Round 8 | No new P0/P1, merge allowed. All lines of checklist against plan are done or for next step. One P2 (do not leave intermediate commits) left as PR comment |
