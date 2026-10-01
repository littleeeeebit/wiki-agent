# Step 4 — Review Cell and Loop

The relationship between the overall design and the steps is in [Overview](0-overview.md)]. It stands upon the reconnection and session allowance of [Step 2](2-agent.md)], and the specification and gate execution of [Step 3](3-spec.md)].

Goal. A single PR goes through review rounds without human intervention until it is "mergeable". The only places waiting for a human are write approval and [Merge]. The procedure of `operator/codex-review-loop` runs between two sessions inside the app instead of a terminal.

## Agreements with the User

2026-09-25.

| What | Agreement |
| --- | --- |
| Review result | The review cell writes nothing and produces the result as the final answer. The server writes that answer into a result file |
| Round file | `raw/review/<repo>/<pr>/` of the hub. Not placed in `review_dir` of the worktree |
| Disagreement | The work cell submits fixes, non-reproducibility, or objections with evidence for each discovery. The server puts it into "Handling of previous discoveries" in the next round's instructions. If the same discovery remains an objection for two consecutive rounds, it stops and hands it over to a human |
| Default value | Round limit 12, concurrency 3. Changed in settings |
| Loop start | Specification work sends round 1 as soon as the PR is written and there is a spot. The "Review Loop (N)" button is for PRs without specifications and stopped loops |
| PR without specification | The server creates a minimal specification from the PR. Title is `goal`, body's `변경 이유` is `decisions`, `done` is one `gate_cmd` |
| Merge method | squash |
| Original checkout | In post-merge cleanup, only `git merge --ff-only` if the original is on the base branch and there are no uncommitted changes. Otherwise, it leaves it alone and shows "Original is behind" |
| Review model | Codex base model, effort `high` |
| Approval notification | Rail indicator and OS notification. Once when the loop stops at approval, only when the window does not have focus |
| Remaining P2 | Select only what the review cell can do in the round where merge permission is granted. A draft comment appears next to [Merge] and the server attaches it when merging |
| Loop and project switching | The loop runs with its own repository. It does not stop even if switched (review round 1) |

Two points differ from the overview. The round file is placed in the hub, not `review_dir` — `git add -A` of the work cell can commit the round file, and if the worktree is deleted, the record also disappears. The ff-only of original checkout is the second exception where the server writes to the original. It is recorded together in the safety boundary of the overview and the boundary of the wiki-agent overview.

## Review Cell

One per worktree. `ChatSession(worktree, system=<리뷰 프롬프트>, model=<설정>)`. This is independent of the implementation cell. Ordinary review is read-only; Cloud verification review also executes checks and creates verification files.

- Ordinary review exposes only reading, file discovery and source search. Claude uses `Read,Glob,Grep`; Codex uses `repo_read`, `repo_glob`, `repo_grep`, bounded to tracked source without private files or symlink escapes. Codex's shell and inherited MCP/apps/plugins/browser/computer-use tools are disabled for this profile. Its thread and every turn use read-only sandboxing and `approvalPolicy: never`
- Cloud verification review has `Bash,Read,Glob,Grep,Edit,Write`, independently of the implementation cell. Codex enables shell execution with `workspace-write` and `approvalPolicy: never` on its thread and every turn. Writable roots are the dedicated review checkout and `raw/review/<repo>/<pr>/verification/<head>/`. It never uses unrestricted access; inherited MCP/apps/plugins/computer-use and permission-escalation tools are disabled. On Windows, this cell alone uses the documented `unelevated` sandbox fallback, without editing global account configuration. Claude preapproves these tools in default permission mode, retaining CLI deny rules
- The Cloud reviewer must execute verification checks and may create test scripts, fixtures and receipts in that artifact directory. It does not edit tracked implementation, commit or push. Generated artifacts remain outside source so later rounds and gates stay clean. The server checks the source checkout after review and before publishing success; an altered checkout stops for user inspection instead of being accepted as the reviewed commit. Configured server execution and final gates remain required
- The review cell's conversation continues while the PR is alive. The next round goes to the same session that remembers the previous round
- Write the CLI session id and tools profile in `raw/review/<repo>/<pr>/session.json`. It continues even if the server is restarted. A session saved under another permissions/tools profile starts a new conversation once; existing rounds and findings remain recorded

The prompt is `tool/prompts/review-round.md`. It translates "what the instructions should contain" of `operator/codex-review-loop` as is, but tells it to write the final answer instead of a result file. The first line is `Round <n> · PR #<pr> · <머리 커밋 7자>`.

## One Round

1. The server writes instructions — `raw/review/<repo>/<pr>/round-<n>.md`
   - Round number, PR number, head commit, base branch
   - Allow list. Source reading and search; server-provided PR diff and check receipts. Cloud verification also permits command execution and verification files. Deny list is after that
   - Actual number of `git diff --shortstat <base>...<head>`
   - Handling of previous round discoveries. The `disposition` block of the work cell as is
   - Already run. Server's gate results
   - `Deferred P2`. Instruction not to report again without new evidence or grade change
   - If the number of discoveries has not decreased for two consecutive rounds, insert a "Discovery grouping" clause
2. One turn to the review cell, carrying the recorded instruction's contents directly. The hub's round file remains the audit record; the reviewer does not need filesystem access outside its worktree to read it
3. The server writes the answer to `round-<n>-result.md` and parses it
   - The first line must be `Round <n>` and the head commit must match
   - Discoveries are lines starting with `^\[(P0|P1|P2)\] (\S+):(\d+)`. The lines below are the body
   - The last non-empty line must start with `머지 허용` or `머지 불가`
   - If it deviates, the round fails. Send it again once, and if it fails again, `멈춤 — 라운드 형식`
4. If `머지 허용`, the loop ends. The specification is `머지 가능`. Go to P2 selection below
5. If `머지 불가`, send P0·P1 to the work cell in one turn. P2 is written in `Deferred P2` and not sent. The work cell's instructions are exactly as in the procedure document's "what the receiver holds" — reproduce first, fix the pointed place and count other places affected by the rule separately, if you disagree, provide evidence. `disposition` block at the end of the answer
   `[{finding, action: "fixed" | "not-reproduced" | "disagree", evidence}]`
6. The server runs the gate (executor of step 3). If it fails, send the tail of the output to the work cell in one turn and run it again. If it fails twice in a row, `멈춤 — 게이트`
7. If it passes, push. Next round

Writing by the work cell is approved one by one as in step 2. Tools and commands allowed as [during session] pass without asking. The gate command that the work cell runs itself after fixing is also not asked every round if allowed once as [during session].

## Status

The status values of the specification are all in the table of [Step 3](3-spec.md)]. What the loop uses are `리뷰 대기`, `리뷰 Rn`, `고치는 중 Rn`, `머지 가능`, `머지 대기`, `머지됨`, `멈춤`.

The reasons for stopping are all in the table below. The overview and other step documents point to this table (review round 6). In the code, this table is one enum, and if the loop tries to stop for a reason not in the table, the test turns red. The reason for `멈춤` is not `승인 대기`. A loop waiting for approval is not stopped but standing, and is shown separately as `waiting: true` of the specification.

| Reason for stopping | Restart |
| --- | --- |
| `라운드 상한` | [Continue] increases this limit by 4 only in this specification |
| `게이트` | [Continue] after a human talks to the work cell |
| `반론` | [Continue] after a human writes what they decided for that discovery. That statement goes into the handling clause of the next instruction |
| `라운드 형식` | [Continue] |
| `사람이 멈춤` | [Continue] |
| `서버 재시작` | [Continue]. When the server starts, it changes all specifications in loop status to this |
| `저장소 없음` | `repo` of the specification cannot be resolved to a path. [Continue] when that repository returns |
| `작업트리 없음` | `worktree` of the specification is not in the list of that repository. [Continue] receives it again from the PR as `adopt` and continues |
| `검토하지 않은 base 에 머지됨` | No [Continue]. End with one of the two buttons under "End of merge with mismatched base" below |
| `머지 대기에서 빠짐` | [Continue] reads the PR again. If `OPEN` and head and base are the same as the allowed round, `머지 가능`, if `OPEN` but different, `리뷰 대기`, if `CLOSED`, leave it stopped with 409 "Must reopen PR on GitHub" |
| `로컬 검증 준비` | Prepare the cloud handoff, local environment or required evidence, then resume local verification |
| `외부 수정 대기` | Fix in the external implementation environment, push the new head, then start the next local review round |

`rounds` of the specification is `[{n, head, base, findings: {P0, P1, P2}, verdict, gate, disposition}]`. `base` is the base branch name (`baseRefName`) of the PR that the round saw.

## When head or base moves

The round looks at one head commit and one base branch. If either moves while the review cell is reading or after permission, that judgment becomes stale. Base changes to `gh pr edit -B`. If only the head is compared, the same commit is merged into an unreviewed base (review round 2).

Base branch moving forward (merge of another PR) is not counted as stale. If even that is blocked, the round must be run again whenever there is a merge in the base. Conflicts are rejected by GitHub in the merge.

- The first round of specification work goes to `리뷰 대기` after the PR post-step (plan row commit and push again) of step 3 is finished. It is not the moment the PR stands. If that turn overlaps with the first round, the first round sees the head before the plan row commit (review round 1)
- Still, a human can push or change the base on GitHub. After parsing the result, if `gh pr view <n> --json headRefOid,baseRefName` is different from `head`·`base` of that round, discard that round and send it again with the new head. The discarded round is not counted in the limit and is left in `rounds` as `stale: true`

## Project and Loop

The server's project selection is one. The loop does not rely on that selection (review round 1, user's decision).

- `repo` of the specification is the project name according to the contract of step 3 (same as the folder name of `raw/specs/<repo>/`). When the loop starts, it sets the original path once as `channels.repo_for(repo)`, and all subsequent calls (`gh`, `git`, gate, session) use that path and `worktree` of the specification. If the name can no longer be resolved to a path, `멈춤 — 저장소 없음` (review round 2). It does not call `current_repo()` and `work.ours()`. Since `ours` now looks at the list of selected projects (`tool/main/work.py:44`), it rejects the loop's worktree after switching
- When the loop starts and every round, it checks if `worktree` of the specification is in `workspace.worktrees(<그 경로>)`. If not, `멈춤 — 작업트리 없음`
- The path where the server once confirmed and created a session belongs to that session. Step 2 decided the answer of `events`·[Stop]·approval with this rule. Here, `/api/work/log` is also expanded with the same rule. New instructions, clearing, and deleting only receive those of the selected project as now
- Switching splits waiting. `configure` now waits for all noise with one `work.busy()` (`tool/main/query.py:306`). Noise is a mix of running turns and short requests (create·delete·clear)
  - Running turns and loops do not wait. The reason for waiting was "if switched, the worktree of the approval waiting disappears from the list and the answer is 404", and the above rule receives that answer
  - Short requests wait as now. Create reads the selected repository, releases the lock, and runs `git worktree add` (`tool/main/work.py:121-131`), and delete reads the repository again between `ours` and `remove(current_repo(), …)` (`:139-149`). If switching intervenes in between, the old project's worktree goes to the new screen's response or another repository goes to `remove`. This is the competition blocked by review round 3–5 of the wiki-agent step 6 (review round 2)
  - So, attach types to noise. `hold(..., kind="turn" | "short")`. `configure` rejects if there is even one `short` and does not see `turn`
  - All requests that catch determine the repository when catching and use it until the end. `say` now releases the lock after catching and calls `ours(body.path)` (`tool/main/work.py:249-251`). If a switch that does not see `turn` intervenes in between, `ours` gives a 404 while looking for the old path in the new project's list (review round 3). So `say`·`make`·`reset`·`clear` do `repo = current_repo()` and `hold` together inside `with _lock:`, and path confirmation is done in the list of that repository with `ours(path, repo)`. Since switching does not delete the worktree, switching after catching does not change what this request does. Records (`remember`) are not mixed into the new project side because the path is the key (`tool/main/work.py:53`)
  - `_busy` of query focus remains the same
- The rail places a "Other projects" bundle under the row of the selected project. Running turns and loops of other projects appear with repository name and status. If clicked, it opens that worktree on the right side without switching. Approval notifications also come regardless of the project

## Concurrency

One loop is one thread. `threading.BoundedSemaphore(설정)` gives a spot. Specifications waiting for a spot are `리뷰 대기`. The thread launches a turn with `Run` of step 2 and waits for its end. Since the screen is attached to the same buffer, turns sent by the loop also look like turns sent by a human.

## Open PR and Fetching

`GET /api/prs`.

```
gh pr list --state open --json number,title,headRefName,headRefOid,headRepositoryOwner,isCrossRepository,url
```

- Reads again when the window receives focus and when the loop status changes
- If `isCrossRepository` is true, show "Fork — nowhere to push" on the row and it cannot be selected
- A PR with a specification becomes a row of the specification. A PR without a specification creates a minimal specification when selected

A PR without a worktree is received with `workspace.adopt(repo, branch, oid)`. Added to the public entry point of `workspace`.

1. `git fetch origin <branch>`
2. Folder name is the branch changed to `TASK` shape. Keep the branch name as is
3. Check if there is a branch of that name locally (`git rev-parse --verify refs/heads/<branch>`). It is common for a PR uploaded by hand to have the local branch remaining but no worktree, and `-b` fails there (review round 1)

   | Local branch | Action |
   | --- | --- |
   | None | `git worktree add --track -b <branch> <path> origin/<branch>` |
   | Exists and same as `oid` | `git worktree add <path> <branch>` |
   | Exists and different from `oid` | Reject. Show "Local `<branch>` is different from PR head" and the two commits. Since commits only in local can be deleted, the server does not match them |
   | Checked out to another worktree or original checkout | Reject. Show git's reason as is |

4. If it is a newly created branch but `HEAD` of the worktree is different from `oid`, clear the worktree and that branch and reject. Do not clear an existing branch

## [Merge]

`POST /api/specs/{id}/merge {head}`. `head` is the head commit the screen saw.

The merge is bound to the commit allowed by the review. Not the screen's commit. `--match-head-commit` compares only the passed commit and the current PR head. If a new commit is pushed after permission and the screen reads the list again, `head` of the screen becomes an unreviewed commit and is merged as is (review round 1).

1. `approved` is `head` of that round when the last round is `머지 허용`. If none, 409
2. If `head` of the screen is different from `approved`, 409 "New commit after review". Return the specification to `리뷰 대기` to receive a new round. If `gh pr view <n> --json baseRefName` is different from `base` of that round, it is the same 409 "Base change after review"
3. `gh pr merge <n> --squash --match-head-commit <approved>`. If there was a push in between, GitHub rejects it, and the specification goes to `리뷰 대기` for the same reason
4. Check after merge. Command exit code 0 is not a merge. In a base using merge queue, if mandatory checks remain, auto-merge is turned on, and if passed, it just enters the queue, so the PR is still open (`gh pr merge --help`, review round 4). Read `gh pr view <n> --json state,mergeCommit,baseRefName`

   All cases are divided by `state` and one condition. Two lines never match one response (review round 9)

   | `state` | Condition | Action |
   | --- | --- | --- |
   | `MERGED` | base is same as allowed round | To 5. If `mergeCommit` exists, write it in the specification, if empty, go empty. Cleanup does not use this. The place that uses it is one of passing in step 5, and that side reads it again in its own step |
   | `MERGED` | base is different | `멈춤 — 검토하지 않은 base 에 머지됨`. OS notification and PR comment. Do not do 5 or less — must leave worktree and branch so a human can revert |
   | `OPEN` | In queue or auto-merge turned on | `머지 대기`. Do not do 5 or less. Read this table again when the window receives focus and every 1 minute |
   | `OPEN` | Not in queue and auto-merge turned off | `멈춤 — 머지 대기에서 빠짐`. Do not do 5 or less |
   | `CLOSED` | — | `멈춤 — 머지 대기에서 빠짐`. Do not do 5 or less |
   | Read failure | `gh` fails or no `state` in response | Read again after 1 minute without changing status. Do not do 5 or less |

   Queue and auto-merge are seen by whether `gh pr view --json autoMergeRequest` is empty and `pullRequest.isInMergeQueue` of `gh api graphql`. `gh pr view --json` does not have `isInMergeQueue`
5. If there is a P2 comment draft, `gh pr comment <n> --body-file`
6. Write the specification to `머지됨`, result row "PR #n merged — round k, remaining P2 j"
7. Cleanup. `git fetch` in original checkout. If original is on base branch and `git status --porcelain` is empty, `git merge --ff-only origin/<base>`. Otherwise, leave as "Original is behind" and move on
8. `workspace.remove`. If original moved forward, `merged` becomes true and deletes even the branch. If original is behind, delete only the worktree and leave the branch — judgment as now
9. Delete remote branch only when it is still on the merged commit. `git push --force-with-lease=refs/heads/<branch>:<approved> origin --delete <branch>`. If someone pushed to the same branch after merge, git rejects it, and the branch remains. Rejection is not a failure but "New commit on remote branch — kept". If deleted unconditionally, unmerged commits are lost on remote (review round 1)
10. Close the review cell. `raw/review/<repo>/<pr>/` remains

Base cannot be blocked, only noticed. GitHub's merge binds only the head commit atomically. The guard of GraphQL `MergePullRequestInput` is only `expectedHeadOid` (confirmed with 2026-09-25 `gh api graphql`), and REST is also only `sha`. If someone does `gh pr edit -B` between 2's confirmation and 3's merge, that merge stands (review round 3). If the server pushes a squash commit directly to the base, it can bind up to the base atomically with `--force-with-lease`, but the PR becomes not merged on GitHub, so the merge record that `harvest` reads disappears and it skips the base's protection rules. So narrow the gap to a few seconds, and if it occurs, 4 catches it

### End of merge with mismatched base

While `멈춤 — 검토하지 않은 base 에 머지됨`, worktree, local branch, remote branch, and review cell are all as is. `POST /api/specs/{id}/settle {choice}` ends it. There are two buttons on the specification card (review round 7).

| Button | `choice` | Action |
| --- | --- | --- |
| [Accept] | `accept` | Human accepted the merge to that base. Leave specification as `머지됨`, write "Merged to unreviewed base `<실제 base>` — accepted" in the result row, and do 5–10 of [Merge] with the actual base. Cleanup is still done only in `머지됨` |
| [Re-PR] | `reopen` | Upload a new PR to the original base. Whether to revert the merge of the wrongly entered base is not a condition — new PR is a separate PR going to the original base, and what to do with the wrongly entered base is that base's business. Server does not write to that base. Write "Merge of `<실제 base>` remains. To revert, on GitHub" next to the button (review round 8). Clear `pr` of the specification and return to `작업 중`. Since worktree and branch remain, step 3's completion judgment (`done-report` → gate → PR) uploads a new PR to the original base, and the loop runs again with the new PR |

Both are received only when the specification is this stop. Otherwise 409.

[Re-PR] redraws the boundary of the review. Round and permission belong to one PR (review round 8).

- Move `rounds` of the specification to `history` as `{pr: <옛 번호>, rounds, closed: "검토하지 않은 base 에 머지됨"}` and clear `rounds`. `approved` comes from `rounds`, so it disappears together
- The round limit increased only in this specification (+4 of [Continue]) returns to the default value
- Close the old PR's review cell. The new PR receives a new number `raw/review/<repo>/<새 번호>/` and a new review cell. The old folder remains

## P2 Selection

Send one more turn to the review cell in the round where `머지 허용` appeared. "Select only what is doable among Deferred P2 and issue as `p2-keep` block. Discard trivial styles and tastes." What is selected is written in `p2` of the specification. The next work focus of step 3 reads this as candidate material.

## Screen

Temporary. Step 6 rebuilds it.

- "Review Loop (N)" on the rail. N is the number of selectable PRs not in the loop. If one, start immediately, if many, multi-select modal. Fork PRs are dimmed with reason
- `#번호` and `R<n>` on the rail row, `wait` dot if waiting for approval
- [Review] tab on the right side — judgment per round, number of discoveries, open result file
- [Merge] and P2 comment draft on `머지 가능` specification
- The server streams loop status changes as one stream (`GET /api/loops/events`). The screen fixes the rail and notifications with this one
- OS notification is `tauri-plugin-notification`. New dependency. In browser, use `Notification`, if no permission, only rail indicator remains
- Three setting values (round limit, concurrency, review model) are in `raw/chat/main.json`. Three temporary input fields. Step 6's setting modal takes them
- Delete review focus. Records remain

## Test

`test_loop.py`. Both cells are bandwidth.

| What | Case of becoming red |
| --- | --- |
| Round parser. First line number·head commit mismatch, no last line is failure | If checks are deleted one by one |
| Stop at `머지 허용` and `머지 가능` | |
| Stop at limit, stop at gate failure twice in a row, once is continue | If consecutive judgment is deleted |
| Stop at same discovery twice in a row `disagree` | |
| Previous handling, `--shortstat`, `Deferred P2` go into instructions. Grouping clause if discoveries do not decrease | |
| Fourth at concurrency 3 is `리뷰 대기` | If semaphore is deleted |
| `adopt` clears and rejects if head commit is different | If comparison is deleted |
| Fork PR cannot be selected | |
| [Merge] passes screen's head commit as `--match-head-commit` | If argument is removed |
| Cleanup does not ff on dirty original | If check is deleted |
| Loop status after server restart is `멈춤 — 서버 재시작` | |
| Stop reason is same enum as this document's table. Failure if stopped for reason not in table | |
| `accept` of `settle` cleans up only after `머지됨`, and `reopen` returns to `작업 중` without deleting worktree and branch. 409 in other statuses | If status check is deleted |
| After `reopen`, `rounds` is empty, old round is in `history`, limit is default, new PR's first round is R1 and new review cell | If initialization is removed |
| `Bash` is not in Claude review cell's arguments | If tool list is returned to `READ_TOOLS` |
| [Merge] is bound to `head` of allowed round, not `head` of screen, 409 if different | If comparison is deleted |
| Remote branch deletion loads allowed commit to `--force-with-lease`. Remote branch pushed after merge remains (actual git, local bare remote) | If argument is removed |
| First round goes after plan row push. If head moved after result, discard round and send again | If order or head comparison is deleted |
| Four cases of local branch of `adopt`. Local branches of other commits are neither cleared nor matched | If branching is deleted |
| Even if project is changed, loop sends next round, can answer approval of other project worktree. New instructions are rejected | If loop is made to call `current_repo` |
| If base changed after result, discard round, 409 if [Merge] is also different from base allowed round | If base comparison is deleted |
| Switching is possible even if there is a running turn, switching is 409 while create·delete is catching. Catching request goes to the end with the repository at the time of catching — even if switched immediately after catching `say`, that turn runs with the old repository's path | If type distinction is deleted or repository is read again after catching |
| If base is different from allowed round after merge, `멈춤` and no cleanup | If check after merge is deleted |
| Even if `gh pr merge` succeeds, if PR is `OPEN`, it is `머지 대기` and worktree·local·remote branch remain. Cleanup only after becoming `MERGED` | If merge is judged by exit code |
| Six lines of table after merge one by one — `MERGED` where `mergeCommit` is empty also goes to 5, read failure changes nothing | If lines are deleted one by one |
| `repo` of specification is name and loop sets path as `repo_for` | |

## Things not done

| What | Why |
| --- | --- |
| Loop merging itself | Safety boundary of overview |
| Fork PR | Nowhere to push |
| Allow writing to review cell | User's decision |
| Automatic loop resume after restart | Don't know what changed while human was away. One [Continue] is enough |

## Agreements in implementation

Filled the spots left empty by the plan like this.

The Review tab now shows its independent session's live progress and owns the
review model and effort controls. Settings retains round limits and concurrency.
Completed progress messages are kept separately from the final answer and use
the Korean overlay. The Agent tab continues to show implementation turns only.
An explicit next-round action can review a new remote head or request another
review after permission. External implementation mode never dispatches a local
fixer; cloud implementation additionally requires its local execution evidence.
New external/cloud reviews use detached checkouts so an implementer's existing
branch can remain checked out elsewhere without blocking adoption.

| What | Agreement |
| --- | --- |
| Spot | Condition variable and one number instead of `BoundedSemaphore`. Read concurrency of settings whenever waiting for a spot, changed value is heard from the next waiting loop |
| Gate before round | Check if gate passed at that head before sending to review every round. If `gate` of specification is not pass of that head, run it first. First round of PR received without specification, and head with plan row commit added also go through gate like this. "Already run" in instructions is always the result of that head. Changed on purpose by [reliability PR 2](../reliability/2-tests.md): a round now runs only the registered checks its changed paths map to, widening to the whole gate when unsure, and the whole gate runs once on the allowed head before `머지 가능` — the review and the final gate are bound to the same commit |
| Push elsewhere | If human pushes to GitHub side branch and worktree is ancestor of PR head, make worktree follow `--ff-only` before round. File read by review cell must be same as head being reviewed. If split, push is rejected and `멈춤 — 게이트` |
| Push failure | `멈춤 — 게이트`. Status `고치는 중` is "including gate and push", no separate reason in table |
| Review cell failure | If cell cannot answer (process died, could not read Codex list), send again once like format failure and `멈춤 — 라운드 형식`. Unexpected exception in loop also attaches "Loop broken — …" to `라운드 형식`. Either way, one [Continue] is enough |
| Merge permission and discovery | If last line is `머지 허용`, it is permission even if P1 is written. Judgment is review cell's |
| `머지 불가` with only P2 left | Send one turn to work cell even if no P0·P1. Load judgment line. If not sent, same judgment runs until limit |
| Same discovery | Same discovery if file is same, and line or text after `—` is same. If review cell changes text and moves line, it misses it, and then limit stops |
| File of discarded round | Rename to `round-<n>-stale-<머리 7자>.md` and `-result.md` and keep. Next attempt of same number uses `round-<n>.md` |
| After [Merge] | If `gh pr merge` ends with 0, set specification to `머지 대기` first and read table. Even if reading fails, 1-minute poller and window focus read again |
| Remote branch already gone | If repository setting deleted at merge, write as "already gone" and move on |
| Name of PR without specification | Folder and specification `id` is head branch changed to `TASK` shape (`workspace.folder_for`). Branch name remains in `pr.branch` of specification and push and PR creation use it. If there is a specification of same name, do not receive |
| Base of [Re-PR] | Write base of allowed round in `base` of specification, and step 3's PR creation uses it instead of default branch |
| Work cell's model | Write model selected by [Start] in `cell` of specification. Turn loop sends to work cell opens session with it |
| Turn and list launched by server | `GET /api/loops/events` closes two empty spots left in step 3 window confirmation. Every time specification is saved, every time server launches turn, one line goes, and screen attaches again if it is window opening that worktree and reads list again |
| Notification | Once when specification of loop status becomes waiting for approval, once when merged to unreviewed base. Only when window does not have focus and there is permission. Permission is asked when pressing "Review Loop" |
| Read | `/api/file` is same as `/api/work/log`, read regardless of selected project if it is path where server created session |

## Confirmation

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- From window to this repository. One specification to PR in step 3 → loop starts itself → work cell fix and approval in three or more rounds → refresh window in middle and attach again → [Stop] and [Continue] → `머지 가능` → [Merge] → cleanup
- Receive PR uploaded by hand with "Review Loop" and see if worktree stands on PR's head commit
- See if decision record is created in next `sync` after merge

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Review cell | Prompt, session, round file, parser | Done |
| 2 | Loop | Status, one round, stop and continue, concurrency | Done |
| 3 | PR | List, `adopt`, minimal specification | Done |
| 4 | Merge | [Merge], P2 selection and comment, cleanup | Done |
| 5 | Screen | Button and modal, rail, review tab, notification, temporary settings | Done |
| 6 | Gate | All confirmation above | In progress — auto confirmation passed, window confirmation remaining |
