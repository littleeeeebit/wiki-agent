# Task lifecycle recovery

The task flow must start without discarding compatible edits, review the PR
actually selected, and finish only when merge cleanup is complete. This
investigation covers the defects reported on 2026-10-04 and error diagnostics.

## Evidence and causes

| Report | Observed implementation cause | Change |
| --- | --- | --- |
| Review rejected because its branch is already checked out | `workspace.adopt` always ran `git worktree add` for a local PR branch, even when it was open in the selected repository | Reuse the clean checkout at the verified PR head, under the existing checkout reservation |
| Review stopped without merge and cleanup | `allowed` ended at mergeable; shared-branch cleanup explicitly retained the local task branch | Continue from independent approval through the existing final gate and guarded merge, then return to the base and remove reviewed refs |
| Cleanup stopped permanently after one failure | `finish` marked the task merged before cleanup; the poller visited only merge-waiting tasks | Persist completion separately and retry merged tasks whose cleanup remains incomplete |
| The Cleaned heading and completed tasks stayed visible | The draft group used the internal `정리됨` label, and merged tasks remained while their shared checkout existed | Label drafts Start pending and hide completed cleanup independently of checkout existence |
| A new specification could not start with local changes | `workspace.create` rejected every nonempty porcelain status | Let `git switch -c` preserve compatible staged, unstaged and untracked edits and refuse actual overwrite conflicts |
| No model shown in a task's Agent pane | Responsive CSS hid the model controls; no independent selected-model label existed | Keep the selected label visible outside the collapsed controls |
| Claude quota percentage missing | Only SDK rate-limit events were read; these can omit utilization; the no-session path always returned empty quota | Read public quota fields with the existing CLI login and retain event samples for matching windows |
| Added/deleted diff lines had no color | `text-add`, `text-del` and their backgrounds referenced undefined Tailwind color tokens | Bind the existing green addition and red destructive tokens; keep file headers neutral |

The affected checkout was inspected read-only. At investigation time it was
already on another task branch with unpublished edits, rather than the branch
in the reported refusal. Those files were preserved.
The originally reported failure is reproduced by an isolated real-Git regression
whose selected checkout is already on the PR branch, without an existing spec.

## Claude quota provenance

The vendor's [headless SDK quota issue](https://github.com/anthropics/claude-code/issues/50518)
reports missing utilization on normal allowed events. The vendor's
[status-line reference](https://code.claude.com/docs/en/statusline) documents
percentage and reset fields, while its [OAuth usage endpoint report](https://github.com/anthropics/claude-code/issues/30930)
identifies account quota polling and throttling behavior. The implementation
therefore caches successes and failures for five minutes, closes HTTP responses,
uses the selected CLI login, and never rewrites or refreshes credentials.

A live read using the current CLI login returned five-hour and weekly
utilization windows. That confirms the data path on this Windows
account, rather than universal access for API-key logins or other platforms.
OAuth utilization is already a percentage; SDK event utilization is a fraction.
Credential values are absent from the result and diagnostic records.

## Recovery boundaries

Git still refuses an overwrite conflict. An independent reviewer must never
inspect unpublished edits as if they were the remote PR's head. Cleanup retains
newer local or remote commits and retries rather than forcing their removal.
It verifies squash-merged content, uses expected-head deletion for local refs,
and a lease for remote refs. A base open in another checkout uses the existing
checkout-local tracking branch, preserving that sibling's files and ref.

PR artifacts are removed only from the validated per-PR review folder. Other
tasks, APK builds and unrelated runtime data are not part of that cleanup.
Specs retain requirements, verdicts and deferred P2. Logging is a rotating local
record shared by HTTP, browser and background failures, separate from disposable
review artifacts and optional Langfuse tracing.

## Verification scope

The automatic review/merge/cleanup regression uses real Git, a temporary bare
origin, a simulated GitHub endpoint and simulated implementation/review models.
It verifies checkout reuse, exact reviewed head, main checkout restoration,
local/remote ref removal and per-PR artifact removal. A second scenario preserves
dirty files through merge and resumes cleanup from freshly loaded metadata.
Staged, unstaged and untracked startup edits and overwrite conflicts use real Git.

Browser checks use synthetic data and the built frontend. In dark mode,
addition/deletion RGB values are `(127, 191, 154)` and `(217, 112, 94)`; in light
mode they are `(47, 107, 79)` and `(168, 58, 44)`. The selected model stays visible
with collapsed controls. These checks do not merge a live user's PR or invoke
a second real model session as an independent reviewer.

The broad backend run completed with 1,436 passes, two skips and five failures.
Two failures were old assertions retaining a merged survey branch; these now
assert deletion and completed cleanup. Two tests exceeded the existing
ten-second `settled` wait. One evidence test observed `unavailable` rather than
`uncertain` translation status; its original reason was not captured. All five
cases passed together on the current-code rerun (33.91 seconds). The latter
three failures did not reproduce, so this is not recorded as a clean full-suite
run and the translator was not changed on that evidence.
The rerun interpreter remained alive after printing its five-pass summary and
was stopped after verifying its process identity; it did not exit normally.

The final expected-head cleanup regression also preserves a local empty commit
added after review in a legacy worktree. It and the existing worktree tests
passed together (16 tests). The frontend build, frontend lint (zero errors,
13 existing warnings), task and mobile browser checks, Ruff, wiki lint,
whitespace checks and UTF-8-without-BOM checks passed. The model label's line
height was fitted to the 320px mobile reading-space check without hiding it.
