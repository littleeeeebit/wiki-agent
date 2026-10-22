# Independent review before production completion

The English-bank task requires an independent equivalence verdict before data
application, while its full completion requires the applied production result.
After a revision without a completion report, the app sent `REPORT_AGAIN`, saying
that nothing could be checked or reviewed and demanding every completion item.
That instruction incorrectly described review startup: the existing Review Loop
control already adopts a manually published PR before `done-report`.

The missing step was the prerequisite reading itself. Ordinary review collects
acceptance evidence and judges the whole completed candidate. An independent
verdict needed before that evidence exists must be a separate stage of the same
authorized loop, rather than a fabricated passing completion report.

## Declaration and lifecycle

A local task can include this top-level field in its spec or `spec-update`:

```json
{
  "prerequisite_review": {
    "instructions": "docs/packet/README.md",
    "inputs": ["docs/packet/rewrite.json", "docs/packet/originals.json"],
    "result": "docs/packet/review.json"
  }
}
```

Instructions and inputs must be committed tracked files within the checkout.
Absolute, hidden and escaping paths, duplicate inputs and a result equal to an
input are refused; symlinks and unpublished input changes are refused at execution. The
declaration grants no command execution, production permission or paid budget.
The existing explicit Review Loop control starts the workflow; publishing or
revising the task does not dispatch a reviewer.

The app runs the existing scoped round checks, then asks its separate read-only
review cell for a prerequisite verdict before ordinary acceptance evidence
collection. The instruction includes server-computed SHA-256 values for every
declared input and identifies the actual review cell. For this stage only, the
reviewer returns one `prerequisite-result` JSON object with its `reviewer_session`,
`verdict: pass | fail` and every field the repository's packet format requires.
It writes no repository file and performs no production operation. Unavailable
required checks or incomplete reading cannot justify a pass.

The app retains the answer, reviewer provenance, reviewed head and input manifest
outside the target repository. Malformed or duplicate JSON, wrong identity and
changed revision, inputs or PR head cannot unlock implementation. A failing
verdict stops and is not silently retried for the same input identity.

A pass returns the exact JSON and provenance to the implementation session. It
must commit the designated verdict unchanged and finish every original acceptance
item, honoring existing approvals, conditions and budgets. An absent, partial or
failing full completion report stops; resume reuses the input-bound independent
verdict. The declared output must be committed and match that verdict. After
implementation completion, ordinary review, evidence floors, publication checks
and the one owned final-verification gate still apply. No prerequisite result
sets the task to complete or mergeable.

The prerequisite is bound to the declared request, input bytes and spec revision.
Changing them requires another reading. Tasks without a prerequisite retain their
existing review signatures and revision-report continuation behavior. The feature
does not change another implementation environment into a local writer.

## Verification

`tool/test_prerequisite_review.py` uses an in-process FastAPI client and temporary
Git repositories with a bare origin; model replies and GitHub are simulated. It covers
one-click continuation through independent reading, application evidence commits,
normal review and the existing final gate, along with refusal, incomplete work,
resume, source drift, malformed results and file boundaries. Existing manual-PR
adoption, spec revision and review-contract regressions are checked separately.
These tests do not dispatch a live reviewer, restart the running desktop app,
apply the English bank or establish production/browser acceptance.

The focused handoff suite ran:

```powershell
python -m pytest -q tool/test_prerequisite_review.py --tb=short
```

Result: `18 passed, 1 warning in 176.01s`. The warning is the existing FastAPI
test client's Starlette/httpx deprecation. The published-head regression first
failed without a receipt because review named the old head; refreshing the
published head before dispatch made it pass. A separate duplicate-key regression
first reached the implementer; strict JSON parsing now refuses it before handoff.

The preceding combined regression run passed 23 cases in 236.41 seconds:

```powershell
python -m pytest -q tool/test_prerequisite_review.py tool/test_task_workflow.py::test_spec_update_without_done_report_asks_for_one_then_stops_with_a_fault tool/test_loop.py::test_review_button_reuses_a_manually_published_task_pr tool/test_review_contract.py::test_closed_declarations_and_executable_markdown_keep_code_floor tool/test_review_contract.py::test_spec_generation_and_revision_preserve_explicit_obligations tool/test_review_contract.py::test_legacy_approval_reuse_is_preserved_only_without_new_obligations --tb=short
```

That run preceded the final published-head regression and audit-record path
change; the final 18-case run covers those changes. The six existing revision,
manual-PR and contract regression cases exercise unchanged non-prerequisite paths.
The reviewer system prompt also explicitly permits the prerequisite protocol;
otherwise its ordinary mandatory merge-verdict format conflicts with the new
turn instruction. After that prompt correction, the final end-to-end handoff and
ordinary result-parser checks ran:

```powershell
python -m pytest -q 'tool/test_prerequisite_review.py::test_one_review_request_hands_off_before_full_acceptance_and_preserves_its_gates[complete]' tool/test_review_result.py::test_the_parser_checks_the_round_the_head_and_the_last_line --tb=short
```

Result: `2 passed, 1 warning in 32.73s`. The end-to-end check first failed with the
old system prompt and now checks that the reviewer receives the permitted protocol.
Focused Ruff checks pass for the five changed Python files. Scoped whitespace,
UTF-8 without BOM and LF checks pass. The cleanup audit retained the new suite and
the affected cases in `test_task_workflow.py`, `test_loop.py` and
`test_review_contract.py` and `test_review_result.py`; none is obsolete or
superseded and no test was removed.
The existing controller's line ceiling increases by 13 with a recorded reason
for its new workflow hook; no global limit or duplication allowance changes.
Server-owned round and final verification remain pending.
