# Verified test cleanup after coding-agent refactoring

Test cleanup is a mandatory finishing stage, not permission to delete assertions
or approve new expected values. Ratchet adoption follows cleanup, on the task
branch, before review and the person's merge.

## Research

Searches on 2026-10-07 combined `coding agent refactoring`, `characterization`,
`test suite cleanup`, `snapshot bloat`, `test minimization` and `mutation testing`.
The following are primary sources, not recommendations to install their tools.

| Source | Existing solution and boundary |
| --- | --- |
| [SlopCodeBench](https://arxiv.org/abs/2603.24755) | Measures verbosity and structural erosion during repeated agent edits. Passing tests alone does not measure maintainability. |
| [test-suite-doctor](https://github.com/JoseAntonioNuevo/test-suite-doctor) | Records test coverage and cost before proposing minimization, then verifies against the baseline. Its experimental workflow requires review of every proposed drop; coverage retention does not establish fault-detection retention. |
| [Mutation Testing as a Safety Net for Test Code Refactoring](https://arxiv.org/abs/1506.07330) | Uses mutation testing to check whether refactoring test code preserves its behavior. This directly addresses the missing safety net when tests themselves change. |
| [Vitest snapshots](https://vitest.dev/guide/snapshot.html) and [Jest snapshot guidance](https://jestjs.io/docs/snapshot-testing) | Existing serializers support readable snapshots. A smaller serializer may discard observations, so updating a snapshot is not evidence of equivalence. |
| [ApprovalTests](https://github.com/approvals/approvaltests.python) | Golden masters preserve observed output; differences require inspection rather than automatic approval. |
| [Stryker](https://stryker-mutator.io/docs/) | Mutants that previously failed the tests must not become survivors. A green suite or aggregate coverage score alone does not establish this. |

## Implementation decision

Reuse the current task branches, review, cancellation and resumable step records.
After production steps, run a test-only cleanup step, then a ratchet step. Do not
start either on a run already transferred to an ordinary Agent task.

The default cleanup permits equivalent Python syntax and lossless JSON fixture
formatting. Python's full syntax tree must remain identical, including assertions,
test names, decorators and expected values. Other files remain byte-identical.
Type comments and type-ignore directives are parsed into that comparison too;
deleting a semantic comment is not lossless cleanup.
The model audits bloated fixtures and reports deferred structural changes; a
no-change audit is recorded explicitly, without an empty PR.
No-change steps return a shared checkout to their base or remove their verified
linked checkout before clearing ownership. Existing expected-head branch cleanup
guards preserve later commits and branches still used by another checkout.

Structural test refactoring requires the connected repository's existing
`test_quality_cmd` in its adapter slots. Freeze its command before editing.
It emits `complete: true` and JSON maps `tests` (stable test IDs to `passed` or `skipped`) and
`mutants` (stable mutant IDs to `killed` or `survived`). Require a nonempty,
passing baseline and at least one killed mutant. After cleanup preserve every
test result and every mutant verdict exactly. The command and all non-test
files cannot be edited by cleanup. Reject absent, malformed or weaker evidence.
This is evidence for the measured mutant set, not a universal equivalence proof.
No mutation framework is installed implicitly and no test is dropped automatically.

Run the frozen characterization command and the repository gate before and after
cleanup. Keep before/after byte and line counts and verification receipts on the
step. Require a real reduction in test bytes or lines without growth in either;
do not reward hiding a large fixture on one physical line. Review still owns
semantic acceptance. A rejected patch remains visible for correction.
If review later changes tests, source or the ratchet, the finishing receipt is
no longer sufficient: the controller stops for revalidation in that task.
Document-only review revisions do not replay the cleanup turn.

Adapter slots use strings; the control file list is JSON encoded, for example:

```toml
[slots]
test_quality_cmd = "python tools/test_quality.py"
test_quality_files = '["tools/test_quality.py", "stryker.config.mjs"]'
```

The command marks `complete: true` only after full test collection and all
configured mutants were measured. Missing or false completeness blocks cleanup.
The command prints only its JSON receipt to stdout and exits nonzero on failed
or incomplete collection. Put mutable reporter artifacts in ignored scratch
directories. Do not rewrite tests or snapshots from the quality command.

Ratchet finalization checks an existing baseline before tightening; it never
adopts growth or an exclusion change. A repository without a baseline adopts its
post-cleanup measurements explicitly in its own reviewed PR. Record whether it
was adopted, tightened or unchanged, and run the final gate on that exact head.
Merge preparation tightens again to account for reviewed cleanup revisions.
An unmerged task is never reported as an applied baseline on `main`.

The baseline now also protects test artifacts (`.snap`, `.json`, `.txt`, `.csv`,
`.html`, `.xml`, `.yaml`, `.yml` under test paths). Existing artifacts receive
their current byte and line budgets; new ones default to 256 KiB and the existing
test-file line cap. Bytes prevent a large one-line snapshot from hiding growth.
Tightening lowers both budgets and drops deleted artifacts. It also enrolls
newly added artifacts only when they fit the existing caps;
an over-cap artifact cannot become acceptable merely by tightening the baseline.
An existing artifact budget is never reset to a larger current value. Explicit relaxation or removal
of artifact protection needs a new reason, like the existing source ratchet.
Old baselines gain this policy only through explicit refactor finalization or
initial adoption, not merely because the application starts or a page is polled.

## Boundaries

Default cleanup does not extract helpers, delete duplicate cases or remove UI
snapshot attributes. Those changes need measured fault-detection evidence and
review. Framework reporters are repository-owned: Python, TypeScript and Swift
do not pretend to share a universal test or mutation runner. Failure, cancellation
or a dirty checkout stops safely; restarting does not silently rebase or reset it.

Expanded verification also reproduced four failures on the pre-change debt
implementation: cancellable Windows experiments referenced the removed
`refactor_profile._nt` object. The executor now reuses `common.process.resumed`,
the same helper the refactor test runner uses, while retaining process-tree
containment and cancellation. The existing resume and descendant-cleanup tests
exercise this repair; execution budgets and candidate policy are unchanged.
