# PR 6 — separate document and code review criteria

Use one loop engine with explicit plan, code, and mixed review profiles.

## Work

A specification names the intended artifact/profile; code validates it. File
extensions alone are insufficient: a Markdown runbook is not necessarily a plan,
and code changes must not pass only a plan rubric. Mixed PRs receive both applicable
criteria without a new orchestration engine.

| Profile | Main question | Blocking examples | Completion evidence |
| --- | --- | --- | --- |
| Plan | Can an implementer carry this out and verify the intended outcome? | Missing requirement, invalid source inference, impossible dependency, undefined authority, untestable acceptance | Requirement-to-stage mapping, sources, alternatives, gates, ownership, rollback |
| Code | Does the changed implementation behave correctly? | Reproducible defect, unsafe state transition, regression, invalid evidence/permissions | Diff, runtime behavior, focused tests, final gate |
| Mixed | Do the plan and behavior agree? | Either profile's material failure | Both sets of evidence |

Do not raise a plan finding merely because planned code does not exist yet.
Do not let speculative implementation preference become a blocking plan finding.
Retain P0/P1/P2 protocol, round/PR/HEAD binding, dispositions and stale-review handling.
Record stable finding IDs for recurrence instead of relying only on count/headline.

Planner A authors the initial plan. After handoff, the configured implementation
model revises documents and a separate reviewer checks them. A does not become
the reviser or reviewer by default. Reviewer sessions remain read-only.

## Evidence and exit

The same fixture reviewed as plan/code yields the correct applicable criteria.
Incomplete plans fail for a concrete missing requirement; sound plans do not fail
for unimplemented code. Mixed changes cannot bypass code review. Final gate
scheduling follows PR 2.

## Scope and rollback

No duplicate state machine. Revert profile selection/prompts independently while
preserving old review records.

## Implementation blueprint

### Specification and compatibility

Add `review_profile: "plan" | "code" | "mixed"` and `review_profile_version: 1`
to specs. Default old records and externally adopted PRs to code; never infer plan
solely from .md suffix. Planner-created specs explicitly select plan and name
`artifact_root`. Validate the changed-file set before review: code outside a
plan-only artifact root changes the effective profile to mixed or stops for an
explicit scope correction, never silently skips code review.

Files: `specs.fields/card/view` validate and expose fields; `loop.minimal/take`
initialize external PRs; `loop.instruction` composes the profile rubric;
`loop.cell` binds reviewer configuration; `web/src/lib/api.ts`,
`SpecSummary.tsx` and `Review.tsx` display profile and reviewer identity.
Use existing spec feed and state names.

### Prompt composition

Keep `review-round.md` as the transport/protocol contract. Add
`review-plan.md` and `review-code.md` as criterion sections. A mixed review includes
both once. Instructions carry profile version, immutable reviewed head/base,
requirement IDs and source manifest for plans, prior dispositions and applicable
gate evidence. Do not give the reviewer write tools.

| Grade | Plan rubric | Code rubric |
| --- | --- | --- |
| P0 | Contradictory safety/authority contract, destructive design without required boundary | Existing security/data-loss/crash/contract criteria |
| P1 | Missing required behavior, impossible dependency, unsupported adopted claim, no implementable acceptance | Reproducible correctness defect/regression |
| P2 | Optional alternative, wording polish, nonessential optimization | Existing suggestions |

Plan completeness means every requested behavior maps to a stage and observable
acceptance; it does not mean implementation already exists. A preference for a
different library is not a defect unless the selected one cannot meet constraints.

### Finding identity contract

Preserve the existing human-readable finding/verdict protocol. Add an optional
`finding-meta` fenced JSON block for new profile versions:

```json
[{"ordinal": 1, "existing_id": null, "component": "loop.merge",
  "invariant": "final gate matches reviewed HEAD",
  "trigger": "push after review", "evidence": "path:line"}]
```

The server owns stable IDs, assigns them by ordinal and validates every reference.
A reviewer can refer only to an existing issue from this specification. Existing
legacy rounds parse without metadata but are labeled identity-limited. No arbitrary
UUID supplied by the model creates or resolves an issue. Match exact validated
existing_id first, then exact normalized component/invariant, then mark a possible
match for clarification. Never merge different issues solely by equal counts.

Round records retain the full parsed findings plus IDs, grade and disposition,
not only counts. When metadata is malformed, one format retry is allowed as today;
a failed retry stops. Historical count-only records remain readable and do not
become fabricated recurrence evidence.

### Role ownership and transitions

Planner hands off after initial PR. Close/release its session before document
revision begins. The reviser is the work session for that PR; the reviewer is a
distinct read-only session with its own ID. Changing model settings mid-round takes
effect next round; retain the model/profile version used for the current verdict.
A stale head discards the verdict and cannot resolve findings.

### Acceptance and migration

Extend `test_loop.py` and `test_specs.py`: plan missing acceptance fails; missing
future implementation is not a plan defect; mixed code reaches code rubric;
unknown finding IDs are rejected; stale round does not increment recurrence;
legacy result remains readable; reviewer never receives work permissions.
Use fixture reviewer outputs for protocol tests and one live plan review in PR 10.

Missing fields default conservatively; no rewrite of archived rounds. Rollback
retains records and disables profile-specific prompts without bypassing final gates.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Profiles | Define profiles and finding identity | Not started |
| 2 | Wiring | Wire instructions and independent roles | Not started |
| 3 | Verify | Verify profile selection and stale results | Not started |

## Sources

[Evaluator–optimizer workflow](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
provides a useful role pattern, not a required dependency.
