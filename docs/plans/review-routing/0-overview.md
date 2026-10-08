# Review routing — task-specific criteria and evidence

Purpose. Keep one review loop, but give each task the review criteria and
verification evidence its acceptance requires. A behavior-preserving refactor,
a new implementation and a future plan must not receive interchangeable review
instructions. Even two implementation tasks can require different proof: an
offline contract check, an isolated running API, browser interaction or an actual
native host.

## Why this plan exists

The user's question exposed a gap between a shared review engine and a complete
task-specific review contract. Existing plan/code/mixed profiles, refactoring
characterization checks and Cloud verification already differ. However, those
differences were not composed consistently for ordinary local tasks.

The [research and Jev experiment](../../research/review-routing-and-evidence.md)
supports separating deterministic obligations, semantic recommendations and
evidence validation. The first implementation added selection and validation,
but left ordinary local live collection and native-host collection unfinished.
That narrower increment was an implementation choice, not an agreed reduction
of the user's end-to-end requirement. This plan makes the remaining work explicit.

## The four stages

```text
Spec + actual diff + registered flows
    -> 1. Required obligations
    -> 2. Jev recommendations, initially shadow only
    -> 3. Code-owned composition and per-item provenance
    -> 4. Collect/validate current evidence -> existing authorized review loop
```

Shadow recommendations enter an audit-only candidate contract. They do not change
the enforced contract, trigger collection or authorize review. Later activation
requires its own evaluated policy and explicit rollout decision.

## Steps

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | [Required obligations](1-required-obligations.md) | Deterministic v1 criteria/evidence selection from explicit spec, diff scope, refactor origin and registered flows | Done — bounded baseline implemented; 14 contract checks passed on 2026-10-07 |
| 2 | [Jev shadow recommendations](2-jev-shadow.md) | Grounded context, closed facet/flow recommendations, replayable shadow evaluation | In progress — scoped context, registered-flow advice, persisted preparation/stale observations and exact-request replay implemented; human-reviewed labels and live production-prompt validation remain |
| 3 | [Composition and provenance](3-composition-provenance.md) | Enforced/candidate sets and inspectable selection grounds for every item | Done — v2 compositions, scoped grounds, rejection dispositions, snapshot checks and legacy diagnostics implemented; integration and focused regression checks passed |
| 4 | [Evidence and handoff](4-evidence-handoff.md) | General local live collection, native collection, identity checks and existing-loop handoff | In progress — collectors and live fixtures verified; follow-up cleanup clears the five repository ratchet violations without relaxing ceilings; a passing full final gate on the reviewed revision remains required |

Stage 1 completion means the documented deterministic baseline, not perfect
semantic inference from arbitrary prose. Unregistered runtime requirements remain
unresolved. It also does not mean every selected evidence kind has a collector.
Statuses describe the current working tree, not a committed, independently
reviewed or released revision.

## Requirement coverage and owners

| Requirement | Stage and code owner |
| --- | --- |
| Different questions for plans, implementations and refactoring | 1: `main/review_contract.py`, existing profile/refactor owners |
| Required evidence cannot be weakened by a model | 1, 3: code-owned floors and composition |
| Context-sensitive Jev advice without initial execution effects | 2: existing `decision` transport and `main/knowledge.py` preparation |
| Explain why each criterion, evidence kind and flow was selected | 3: round-owned contract and source references |
| Current-identity evidence, including actual API and native behavior | 4: `main/verification.py` and registered project collectors |
| Preserve authorization, read-only ordinary review, failure ownership and final gates | All stages: existing `main/loop.py` and execution owners |

The [review-profile blueprint](../reliability/6-review-profiles.md) remains the
authority for plan/code/mixed roles. The [local-verification contract](../../local-verification.md)
remains the current Cloud execution contract. This plan extends their shared
owners; it does not replace them with another loop engine or agent framework.

## Completion and exclusions

Completion requires deterministic floors, recorded recommendations and selection
grounds, automatic registered local collection, an actual supported native-host
collector, and stale-evidence rejection through review and merge eligibility.
Simulated protocol checks, isolated live requests, real host interactions and
semantic quality evaluations must be reported separately.

No production mutation, deployment, push, merge, independent-review dispatch or
additional agent is authorized merely by creating this plan. Unsupported hosts
remain visibly unsupported. This work does not close the separate
[recurrence roadmap](../reliability/8-recovery.md) or
[full desktop acceptance plan](../reliability/10-acceptance.md) without their own
required observations. Each stage specifies its migration and rollback boundary.
