# Merge authorization, maintenance and translation modes

The reports on 2026-10-05 require an explicit merge click, an updated local
base and wiki maintenance inside the PR before it lands.

## Observed execution and causes

The running desktop server was identified by its `tool/main --port 8863`
command. Its saved `raw/chat/main.json` contained `translate: true` and
`auto_merge: true`. No translation mode was recorded. The code's defaults
also enabled auto-merge. Both `loop.drive` after approval and `loop.poll`
after recovery called `automatic`, which used the merge API's internal helper
without requiring a user gesture. Review approval therefore became merge
authorization. The prior lifecycle repair deliberately introduced that
policy; this request supersedes it.

The running process retains its imported backend until restart. Its existing
settings API was used to disable `auto_merge`, preserving its other loop
settings. This mitigates the old process while the replacement is verified.

Several saved merged-task records had `cleanup_complete: false` and a
checkout-busy synchronization message. The checkout guard allowed another
task to start after the merged state, even while cleanup remained pending.
Fast-forward cleanup also reported success when local base commits were
ahead, because `git merge --ff-only` can succeed without making the refs equal.
A checkout-local `wiki-base` ref could advance while the actual `main` ref
in a sibling checkout remained unchanged.

Architecture generation ran after confirmed merge and cleanup. Its writer
changed `.omm` files without committing them to the PR. Such changes could
make a later checkout dirty and prevent synchronization. Wiki lint was a
diagnostic, and corpus refresh was a separate manual command; neither was
a required merge-preparation step.

These saved records establish blocked cleanup, not that every reported
merge failure had the same cause. Regression fixtures isolate each mechanism
with real Git repositories and a simulated GitHub endpoint.

## Merge flow

Review approval alone stops at the Merge button. Legacy `auto_merge` values
and `auto_merge_pending` records do not dispatch merges.

The click reserves the clean PR checkout. `maintenance.prepare` rebuilds
the document catalog, repairs lint findings through the existing local work
session, rechecks lint, and refreshes existing `.omm` documents through the
existing staged writer. Only Markdown and known generated files enter its
commit. Code changes, unfinished repair, a failed or cancelled scan and
pre-existing edits block merge. A model answer that breaks the `.omm` contract
gets one retry told the reason; if it is still invalid, the reviewed `.omm`
documents stay as they are and preparation continues. External implementation sessions retain ownership of lint repairs.

The server commits and pushes maintenance to the same PR. Its receipt records
the source head, prepared head, PR, base and specification revision before
push, allowing an explicit retry of a failed push. Preparation is reused only
for that identity. A changed head receives independent review and a final gate;
the saved click authorizes that prepared head and reviewed repairs of it that
change only what preparation itself may (Markdown, `.omm`, the wiki indexes).
On 2026-10-06 every click produced generated `.omm` prose that review refused,
so a strict head binding cancelled every request. A code repair, stop or
specification revision still cancels the request. Restart preserves an interrupted
clicked request, but cannot invent one. GitHub's expected-head merge guard
still binds the actual merge. A failed merge attempt cancels the request,
including a direct click, so the poller cannot retry it unexpectedly.

After GitHub confirms merge, cleanup fetches and fast-forwards the base,
then verifies equality with `origin/<base>`. An occupied base in a clean,
idle sibling checkout is synchronized too. Dirty, busy or locally advanced
checkouts remain pending without deleting their refs. Shared-checkout task
creation waits for cleanup. No architecture scan runs on the base afterwards.

Repositories without `.omm` retain explicit Add .omm. Generated catalog and
repository-map JSON files are included explicitly; unrelated ignored runtime
records are not staged. Ignored documents are excluded from the published
catalog. Stopping a lint-repair turn does not set the server's shutdown event.

## What the final text means

Agent and Review use the provider's terminal `done` reply as `Turn.text`.
Commentary, tool descriptions and command output are separate steps. The
`answered` boundary records where the reply ended; gate output can still
arrive afterwards. Partial translation therefore renders the completed reply
and question text, labels, descriptions and previews. It does not render
progress or tool descriptions. Commands and user-written text retain their
originals in every mode.

Wiki, retrospective and Next Task use the full Korean overlay in Partial
mode, including their interactive tool descriptions. Off disables automatic
translation; Full retains the full overlay. Binary saved settings migrate to
Full or Off, while new screens persist `translation_mode` independently of
loop settings.

## Verification

Real-Git regressions cover legacy auto-merge settings, explicit click before
merge, maintenance publication in the same PR, review and gate on the new
head, lint repair before indexing, blocked preparation, request scope, dirty
cleanup retry, exact local-base synchronization and sibling checkouts.
Models and GitHub are simulated; these tests do not merge a user's live PR.

Browser fixtures check all three mode choices, completed replies, question
options, untranslated progress in Partial, and the full interactive overlay
for Wiki, retrospective and Next Task. A fixture is not evidence of live model
quality.

Results on 2026-10-05:

- Translation API checks: 5 passed, including mode persistence, legacy
  auto-merge suppression and invalid-mode rejection.
- Focused real-Git checks passed for maintenance publication, lint repair,
  blocked preparation, cancellation, receipt reuse, ignored-document exclusion,
  empty-catalog refresh and exact base synchronization.
- Task and mobile browser scripts passed. The frontend build passed; frontend
  lint reported no errors and 13 existing warnings.
- The broad backend run reported 1,530 passed, 2 skipped and 12 failed. It
  started before the survey fixes and fixture updates. Eight failures mocked
  the removed architecture watcher. Three expected stale sibling bases, and
  one expected cleanup without an available accepted base. Those fixtures now
  require synchronized bases, pending cleanup and recovery. The exact failed
  list subsequently reported 11 passed; its remaining second-merge assertion
  was corrected and that case passed separately. All 12 failed cases therefore
  passed in follow-up runs; the entire suite was not repeated.
- Ruff, wiki lint, whitespace and UTF-8 without BOM checks passed.

The live server's auto-merge setting is disabled. Restart is required to load
the new backend; no live PR was pushed or merged during this verification.

## Review repairs and the unprotected-branch report

Independent GPT-5.6 Sol review of PR #75 at `934be95` reproduced interrupted
maintenance leaving catalog files that the next click rejected as user edits.
Preparation now writes a receipt before running maintenance and records exact
file and staging fingerprints on a normal stop, shutdown or tool failure.
A new explicit Merge click may retry those outputs only for the same source
head, PR, base and specification revision. Added or changed user files and
staging invalidate recovery and are preserved. Changed maintenance still goes
through independent review and the final gate. A forced process termination
before the failure receipt is saved cannot establish output ownership; that
case remains blocked rather than treating unknown edits as authorized.

The user also reported `Branch not protected (HTTP 404)` during an ordinary
local/other-environment merge. Status publication had called the Cloud
protection prerequisite whenever local execution settings existed. Ordinary
tasks now publish their successful reviewed/gated status without that Cloud
requirement. Cloud tasks retain strict protection checks. They read the
branch's explicit protection flag first, report a missing rule as setup pending,
and retain API/permission failures as blockers. No repository protection rule
is changed by this repair. GitHub documents the branch protection flag and
the protection endpoint separately in its
[branch API](https://docs.github.com/en/rest/branches/branches) and
[protection API](https://docs.github.com/en/rest/branches/branch-protection).

The user then reported that a clicked merge appeared frozen. The HTTP request
waited for maintenance and cleanup, while the Review tab showed only one busy
button. Merge stages now persist through the existing specification event feed.
The Review tab shows the current stage, elapsed time and recent stage history;
the task rail shows ongoing merge work. The display follows preparation,
publication, independent review/final gate, GitHub merge confirmation, base
synchronization and cleanup. Failures and restart interruptions become visible
blocked stages. Progress metadata grants no merge permission. A held maintenance
request is exercised through concurrent app API reads, and a browser fixture
checks live event updates, duplicate-click suppression, review waiting and failure
recovery at 400px width.

Scoped repair evidence: five ordinary/Cloud protection checks passed; nine
interrupted-maintenance recovery and ownership checks passed. The merge-progress
group passed eight cases, then its remaining concurrent-request case passed
after correcting the fixture to send the approved head. The held-request case
reads the live `.omm` stage from the public specs API before releasing the scan
and observes completed main synchronization afterwards. The broader backend
suite has not been repeated on the repair head; it remains required before a
requested merge. Frontend build, frontend lint (zero errors, 13 existing
warnings), Ruff and wiki lint passed.

The final task browser run passed, including 400px live merge stages,
waiting-review status and failure recovery; the screenshot is the ignored
`artifacts/merge-progress-400px.png`. Its first narrow-screen follow-up reached
the merge assertions but needed to restore the desktop viewport for the
existing context-menu cleanup fixture. That fixture correction is included.
