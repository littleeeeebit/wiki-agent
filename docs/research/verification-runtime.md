# Verification runtime — temporary Git I/O and multiplied execution allowances

On 2026-10-08, two full pytest processes were active on Windows: one started
at 09:13 and another at 10:15. The first was still in review-contract tests
at 10:46. Its log also contained loop failures. These concurrent runs are
diagnostic context, not passing evidence for this change.

## Measured causes

The inherited `TMPDIR` pointed to a third-party application's shared temporary
folder, while Windows `TEMP` pointed to the user's local temporary folder.
Three disposable Git workloads, each initializing, committing, cloning a bare
origin, pushing and fetching, took 8.66 seconds in the former and 3.17 seconds
in the latter. Tests and verification children now select native `TEMP` on Windows.
This is a measured I/O difference; the underlying filesystem or endpoint protection
cause was not established, and no machine security settings were changed.

The two real API review tests initially took 114.58 seconds, including
41.08 seconds of setup and 38.09 seconds of execution for the local case.
After selecting native temporary storage, their combined fixture and execution
time was about 28 seconds. Other active processes make this a directional
comparison, not a controlled whole-suite speed guarantee.

On Windows, the shared Git/GitHub helper used `subprocess.run` with output
pipes. Its timeout path kills the direct process and then calls `communicate`
again without a timeout; an inherited writer can keep that read open. The
replacement uses UTF-8 temporary output files in native `TEMP`, retaining
stdout, stderr, exit status and partial timeout output without creating pipe
reader threads. The real-Git loop fixtures reuse that existing helper. A
regression creates an eight-second descendant holding stdout and verifies
that the three-second metadata timeout returns before the descendant exits.
The isolated control took 8.116 seconds with the original pipe capture and
3.017 seconds with file capture, both configured with a three-second timeout.

The active project's local verification record showed 11–15-minute flows in
sequence. The service gave setup and every flow a fresh 20-minute allowance
and continued after confirmed failures. Its project runner also rebuilt the
browser bundle in each browser flow and ran overlapping phase checks plus
full unittest discovery. Those are real project command costs; the service
must not suppress declared assertions or infer equivalent builds.

Cloud review also called the executor on unchanged valid evidence. Although
individual flows could reuse receipts, setup ran before that reuse decision.
The loop now checks the existing current-evidence validator before dispatching
the executor, retaining all merge and independent-review boundaries.

The native-temp serial probe still exceeded the existing 20-minute gate
allowance while in the review tests. That makes serial execution another
confirmed limit: improving scratch I/O alone cannot make this gate usable.
The probe was interrupted, and is not passing full-suite evidence.

The tests already give mutable Git repositories, settings, logs, homes and
servers their own fixture paths or ephemeral ports. Separate pytest-xdist
processes isolate Python module state and session templates too. A two-worker
pilot of seven runtime regression cases passed in 42.70 seconds. Full runs
now use up to four workers with small-batch load scheduling; focused selections
stay serial and worker crashes are not automatically retried. `-n 0` retains
serial diagnosis. This uses the existing pytest framework rather than a second
test runner. The new development dependency is `pytest-xdist>=3.8,<4`.
See [pytest-xdist scheduling](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).

Work-stealing trials assigned large queues. A 100-case synthetic probe with an
intentional first failure still ran 50 other cases after shutdown was requested.
The standard `load` scheduler with `--maxschedchunk=1` ran four other cases.
Full checks now stop after their first failure and finish only their small
in-flight queues; `--maxfail=0` preserves comprehensive failure diagnosis.

## Changes and verification

Setup and flow commands now consume one 20-minute allowance. Command lists in
the shared judge also consume one allowance rather than renewing it per command.
An already cancelled or exhausted gate cannot spawn a child, and deadline checks
include process startup and completion. Timeout remains incomplete evidence.
Cleanup keeps its independent 30-second bound, and Windows job ownership remains
responsible for killing the command's descendants.

Collection stops after the first confirmed failed flow, persists the failure
and sanitizes its handoff. Flow records include start and elapsed execution
times. Completed evidence can be reused under the existing identity rules.
Project-specific repeated builds and snapshots must move into reviewed setup
commands in that project's own runner; this patch does not edit an unrelated
active PR or claim that its product flows have passed.

`tool/test_verification_runtime.py` exercises deadline sharing, refusal to spawn
spent work, failure short-circuiting with cleanup, cleanup after exhaustion,
native temporary storage in tests and real verification children, and Cloud
setup reuse with environment invalidation.
An additional check covers whole-suite versus focused worker selection.
Existing cancellation tests exercise real Windows descendant cleanup.

The first complete four-worker run took 1,409.48 seconds (23m29s), with
1,753 passes, two failures and five skips. This exceeded the 20-minute gate.
The mobile download test assumed a frontend build existed; it now stages the
tracked installation page in its fixture. A three-correction review test used
a single 30-second wait for four rounds; its wait now scales by round count,
without changing product deadlines or assertions. The worker ceiling was raised
to eight, bounded by the machine's reported CPU count, for a fresh full run.

That eight-worker run took 954.61 seconds (15m54s): 1,749 passes, six failures
and five skips. All six failures exhausted the shared test helper's 30-second
wait for asynchronous review or merge completion under concurrent Git load.
The helper now allows 90 seconds; all product deadlines and result assertions
remain unchanged. The earlier foreground test
still allows 30 seconds per expected round. These diagnostic runs are not
passing full-suite evidence.

After the shared wait repair, another eight-worker run took 942.89 seconds
(15m42s): 1,754 passes, one failure and five skips. The remaining native fixture
treated every `WM_COMMAND` from control 1 as a save, ignoring the notification
code and source handle. It now accepts only `BN_CLICKED` from the Save button;
the negative selector and focus assertions are retained. See Microsoft's
[button click notification contract](https://learn.microsoft.com/en-us/windows/win32/controls/bn-clicked).

The next run took 1,235.82 seconds (20m35s), with 1,754 passes, one failure
and five skips. The native acceptance passed, but the 0.9-second regression
budget expired during setup under load. Setup exhaustion now follows the same
pending-record path as flow exhaustion and still runs cleanup. The regression
explicitly covers both a slow setup and setup-plus-flow exhaustion. Runtime
varies with competing workloads; an exhausted gate must remain incomplete
rather than receiving another per-command allowance.

The original fix checkout had five documented debt ratchet violations; their
ceilings were not relaxed by this performance repair. The application checkout
subsequently cleared them in its separate debt cleanup.

The work-stealing fail-fast trial still drained its assigned queues for
1,959.18 seconds after a test failed. A fresh small-queue load run stopped in
65.37 seconds; its three agent-test failures exposed a separate ten-second
asynchronous completion wait. That test helper now uses the same 90-second
allowance as the loop helper, retaining completion and result assertions.

The next load run stopped in 894.20 seconds (14m54s), with 971 passes, five
failures and three skips. Its failures all exhausted explicit 60-second loop
waits in multi-round local-verification tests. Those functional tests now use
the shared helper instead of overriding it. This is test synchronization under
concurrent Git load, not an extension of product execution deadlines. Neither
stopped run is passing full-suite evidence.

The five affected multi-round tests subsequently passed together with five
workers in 134.90 seconds. A fresh full run then stopped in 199.15 seconds:
539 passes, one failure and one skip. The private-memory translation test
did not reach its fake evidence judge under concurrent disk load. It passed
alone in 2.14 seconds and in the complete eight-worker evidence module
(56 passes, one skip, 13.19 seconds). Its purpose is cache privacy, not the
four-second production normalization ceiling; it now supplies an explicit
test allowance and asserts dossier readiness before inspecting the judge.
The production normalization and retrieval limits remain unchanged.

The final eight-worker diagnostic took 2,098.60 seconds (34m58s): 1,746 passed,
12 failed and five skipped. All 11 new runtime regressions passed. Most failures
were short functional synchronization waits under concurrent Git load; one was
a real cross-worker probe-file race. Another checkout was also running a full
suite. This is failed diagnostic evidence, not a passing final gate. Connection
probes now pass their fixture-owned directory to their child hook; parallel tests
no longer write the same repository probe folder. The default worker ceiling was
reduced to four instead of increasing every wait again.

## Actual Cloud incident and Jev audit

The running service used the other `PycharmProjects/wiki-agent` checkout, not this
fix branch. Its project PR #29 verification at
`cf628f19dc650ee2923b5a99c78063e03d924729` ended with zero review rounds, 12
passing flows and three failed flows. It continued through three more flows after
`verifier-generation` failed. All 15 manifest entries executed Python commands;
Jev did not select or judge any of these checks in that service version.

| Registered flow | Legacy result | Interval since previous completion, including preparation | Jev in legacy execution |
| --- | --- | --- | --- |
| access | Passed | First recorded completion | No |
| question-modes | Passed | 59 s | No |
| request-lifecycle | Passed | 53 s | No |
| controlled-stop | Passed | 43 s | No |
| shared-budget | Passed | 60 s | No |
| budget-recovery | Passed | 27 s | No |
| verifier-runs | Passed | 950 s | No |
| repository-gates | Passed | 1,147 s | No |
| evaluation-release | Passed | 422 s | No |
| consultant-answer | Passed | 1,072 s | No |
| request-history | Passed | 937 s | No |
| verifier-generation | Failed | 844 s | No |
| six-sessions | Failed | 919 s | No |
| cap-exhaustion | Failed | 865 s | No |
| accessibility | Passed | 1,081 s | No |

The project's runner makes a new corpus dump and isolated restore for each dataset
flow. `verifier-runs` logged about 141 seconds of checks within its 950-second
interval. Browser builds took about 20–29 seconds within 844–1,072-second intervals;
builds alone do not explain those delays. Snapshot preparation is not timed
separately in the legacy receipts. The repository-gates log contains overlapping
phase gates and full unittest discovery; discovery alone took 819.8 seconds.

The concrete verifier failure was a 30-second browser timeout waiting for the
question textbox. Its dependent generation then failed because no frozen run
existed. This fix retains those product failures and stops collection promptly;
it does not edit or certify the unrelated Cloud implementation.

Although configuration was active with `jev-1.13.0` and a key present, all eight
recorded shadow attempts were `context_too_large`, with zero model calls. The
context duplicated the full catalog in both flow grounds and registered flows,
then added diff and retrieval passages. Shadow also ran only after all Cloud
flows, and could not select already-enforced flows.

The user explicitly chose Jev selection and judgment, retaining actual API/browser
proof. The new path offers all candidates before execution, batches their closed
choices in one request, preserves explicit flows and known failures, and assesses
validated measured receipts in a second bounded batch. Missing or uncertain
decisions stop pending; they do not dispatch expensive fallback checks. Per-file
diff excerpts prevent early large documents from hiding later implementation.
Optional shadow context no longer duplicates the flow catalog in every ground.

An isolated live read-only selection audit offered the actual 15-flow catalog to
`jev-1.13.0`: one model call, 14,803 reported tokens, 9,850 ms total preparation and
decision. It returned uncertain answers. An intermediate compact-code preparation
exposed English normalization failure within 9,176 ms without a model call.
Code literals now bypass prose translation and retain masked non-English labels.
The final compact request reached Jev in one call with 16,053 reported tokens and
346 ms preparation/decision time; every candidate remained uncertain under the
policy. These isolated audit times exclude earlier Git preparation and do not
establish semantic correctness.
These records demonstrate transport and bounded outcomes, not a valid selection
or semantic policy calibration. Active task records were not edited and no project
checks, PR messages, reviewer dispatch or service restart were triggered by these
audits. Private frozen requests remain only in ignored local artifacts.

The focused routing/runtime run passed 21 checks in 428.40 seconds under concurrent
load, including a 15-flow catalog selecting one actual API flow, measured receipt
judgment, refusal of skipped required/failed flows, missing-key/uncertain holds,
tamper/staleness checks, shared execution deadlines and unchanged-head setup reuse.
Its Jev and GitHub transports are doubles; API requests, Git, commands and owned
process cleanup are real. The final four-worker contract module passed 79 checks
in 205.87 seconds. All nine final routing checks passed in 46.92 seconds, including
the protection of a prior-head failure. Both real child-hook probe tests and the
diff/replay regressions passed in the four-check follow-up (24.09 seconds).
Ruff, wiki lint and diff checks passed on the original fix checkout. This is scoped
verification, not a passing whole-suite or live product review.

## Application checkout integration

The fix was integrated into `C:/Users/dasdk/PycharmProjects/wiki-agent`, preserving
the newer debt cleanup and its 120-second asynchronous test waits. The launcher
starts this checkout's `tool/main` directly, using `python` because it has no
local `.venv`; the changes therefore load on the next application restart.

A fresh process using that Python imported the application and review modules
from the application checkout, verified all seven changed runtime module files
against the fix commit, and checked the shared 1,200-second execution allowance
and each Jev stage's 15-second, one-call limit. Its report is retained in ignored
`raw/review-evidence/p0-fresh-launch.json`. Ruff, wiki lint and the unchanged debt
ratchet passed on the integrated checkout. Already running processes retain their
imported code until restarted; their earlier checks do not certify this revision.
The integrated routing/runtime regression run passed all 20 cases in 127.97 seconds
with four workers; its report is `raw/review-evidence/p0-deployed-focused.xml`.
The real entrypoint also loaded successfully with `python tool/main --help`.
No second full suite was started alongside the existing unrelated gate.
