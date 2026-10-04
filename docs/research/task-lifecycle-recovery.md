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

## Premature review and live-loop interruption

The user's repair agent reported round 2 arriving before its round 1 changes
were published. Read-only inspection of the saved PR 13 records confirmed
that both rounds reviewed `803ae40`, and both retained a null disposition.
Round 1 entered correction at 07:22:11 UTC; round 2 began at 07:22:52 UTC.
The work record between them held an empty terminal answer with no error.
`work.run_turn` accepted it as success, `loop.told` returned an empty string,
and the existing passing gate was reused on the unchanged head. The loop
therefore advanced without a completed correction. This ordering is observed;
why the provider returned an empty answer remains unknown.

The [Claude streaming reference](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)
describes sequential queued messages and response iterators ending at a result.
It does not establish that this particular empty result meant the user's
correction had finished. The repair boundary now validates its own completion:
a nonempty terminal answer, a disposition and evidence for every serious
finding, and a changed local head whenever a fix is claimed. Existing round
checks and push verification still run before the next review.
An incomplete correction gets one continuation in the same round and work
session. A second incomplete answer stops with the reason. Explicit resume
finishes that pending correction before dispatching another review.
Requirements revised during correction invalidate the old review normally.

The same saved task stopped at 07:33:16 UTC with `서버 재시작`, although the
desktop server process observed during investigation predated that time.
A second server's unconditional startup recovery could rewrite the first
server's live tasks. An isolated subprocess reproduction confirmed that
defect. The identity of the process that caused the historical stop was not
established; the user's next-round waiting explanation alone does not account
for the persisted restart reason.

Server startup now acquires exclusive ownership of the hub's `raw/server.lock`
before recovery. A second server fails before touching workflow records.
The lock remains held through shutdown and the file stays in place. It uses
Python's [Windows byte-range locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)
or [POSIX flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock), rather
than treating a leftover file as proof of a live process. A forced-process
termination regression verifies that the next process can acquire ownership.
Startup recovery previously stopped every active loop and required explicit
Continue. This was a real continuity gap, separate from premature round 2.
Recovery now retains automatic intent for active loops and previous restart
stops; after exclusive ownership and startup recovery, normal dispatch resumes
them. Checkout and head checks still apply, pending correction finishes before
a new review, and interrupted final gates rerun. Explicit user stops, gate
failures and other blockers remain stopped. The minute poller reattaches active
states without a driver and exits on server shutdown. Automatic dispatch
rechecks eligibility so it cannot overwrite a user stop made since polling.

Two further failures were reproduced with synthetic execution: an exception
writing the completed work record or publishing its feed notification skipped
the already accepted review/plan completion callback and queued instruction.
Both failures now log their error while preserving that handoff. The Suite
view reads existing scoped records and live cells so completion, failure,
approval waits and interrupted execution are visible separately.

The agent's claim that every stopped task is merely awaiting a manual next
round was unsupported. The work prompt, operator rule and review skill now
state that the server owns continuation and reject saving per-round clicking
as a standing instruction. No unknown instruction file in another repository
was rewritten.

Real-Git regressions hold the repair turn open and verify that no round 2
artifact or reviewer request exists, and that the remote head stays unchanged.
They cover empty answers, unfinished status text, a fixed claim without a new
commit, partial finding reports, unsupported disagreement, and explicit resume.
The models and GitHub endpoint are simulated; the commits and pushes use a
temporary bare origin. The subprocess startup regression retains a blocked
review in the first server while attempting a second startup. These tests do
not alter or resume the user's live task.
Additional real-Git startup regressions verify automatic review and correction
recovery while preserving user stops and gate failures. A poller regression
verifies recovery of an orphaned active loop and prompt shutdown.

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

## Independent review repair

The first independent GPT-5.6 Sol review of PR 70 found two serious P1 defects:
the merge-status observer swallowed its own exceptions before the outer poller
could log them, and a prefix-only diff classifier treated real hunk content
beginning with `+++` or `---` as file metadata. The observer now records the
failure without advancing state; per-file diff colors begin after the first
hunk marker, leaving metadata neutral.

A separate synthetic check exposed redaction after JSON encoding: escaped
quotes could leave a password visible and invalidate the record. Redaction now
walks decoded values and secret-named fields before serialization. A regression
checks quoted, nested and escaped values and parses every resulting JSON line.

The repair checks passed: 20 targeted backend tests, the frontend build and
lint, Ruff, wiki lint, and the task browser test. Browser assertions cover real
`+++`/`---` content and neutral file headers in both themes, plus 400px and
1440px containment.

Fresh native Claude Code and Codex sessions also completed read-only hook
smoke checks without tools, edits, delegation or manual hook calls. Both
reported automatic startup branch/document context and seven automatically
delivered rule summaries, including scoped repair and review ownership.
Claude's event stream independently showed successful SessionStart responses.
The Codex probe explicitly used GPT-6.1 Sol; Claude used its existing default
model only for hook validation, not implementation or independent review.
The options question tool was offered in Codex's Default mode but absent in
Claude print mode. Runtime tool blocking remains untested because the probes
did not attempt tool calls. No CLI settings, trust or credentials were changed.

Round 2 allowed the reviewed repair with zero findings. A separate synthetic
boundary check then found that text-form secrets in quotes were masked only
up to their first space. The shared text redactor now consumes complete quoted
values, including escaped quotes, newlines and values truncated before the
closing quote. Six record-level regressions cover error, traceback and browser
stack fields without using real credentials. This additional safety repair
requires a new reviewed head rather than reusing round 2's approval.
All nine error-log tests passed after this change. Ruff, wiki lint, whitespace
and BOM checks passed, and both fresh native-session smoke probes completed
again with the same observed delivery and the same untested blocking limit.

Round 3 found a distinct ordering defect: keeping only the traceback's last
16,000 characters before masking removed the secret key and exposed its tail.
The implementer's full-file reproduction confirmed this and also exposed the
same issue in the error field when an environment secret exceeded its 8,000
character cap. Both fields now redact the complete decoded value before slicing.
The regression covers both long assignments and long environment credentials,
and a nonsecret case verifies the original output caps still apply.

The [OWASP logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude)
requires masking passwords and access tokens and recommends testing logging
failures and unwanted effects. The specific ordering is an inference from the
two reproductions, not a rule attributed to OWASP: destructive truncation loses
information needed for detection, just as serialization changes the syntax
the detector sees. Processing decoded input first retains those invariants
without adding dependencies or raising persisted size limits.
All 12 error-log tests passed with normal exit. Ruff, wiki lint, whitespace and
BOM checks passed. Both fresh native-session hook probes completed again;
automatic context delivery was observed and tool blocking remained untested.
