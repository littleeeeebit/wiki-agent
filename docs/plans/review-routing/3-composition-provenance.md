# Stage 3 — compose obligations and preserve selection grounds

See the [overview](0-overview.md). Inputs come from
[stage 1](1-required-obligations.md) and [stage 2](2-jev-shadow.md); output feeds
[stage 4](4-evidence-handoff.md).

Purpose. Produce one inspectable review contract without confusing mandatory
requirements with non-authoritative recommendations. Explain each selected
criterion, evidence kind and flow through validated inputs, not a fluent but
unverifiable model rationale.

## Implemented contract

`tool/main/review_contract.py` now selects a v2 contract, records the grounds of
every mandatory item and owns the shared dependency rules. The pure
`review_audit.py` helper composes the audit union and supplies a redacted legacy
or current view; it has no transport, registry, collector or dispatch authority.
The existing loop and spec store persist both compositions before readiness and
in the reviewed round.

Top-level `criteria`, `evidence`, resolved `flows`, `problems` and `digest` retain
their execution meaning. `enforced` and `candidate` use sorted stable IDs;
`items` retains separate ground scopes and all valid origins. `enforced_digest`
aliases `digest`. `candidate_digest` binds the frozen observation, union, grounds,
unresolved candidates and rejection dispositions. No shadow field enters the
execution digest. Archived v1 records remain readable with unavailable provenance;
their old digest cannot approve a newly composed v2 contract.

## Two contracts, one execution authority

Let `F` be deterministic obligations, `J` validated recommendations and `C`
their dependency-closed union. Maintain:

```text
candidate = closure(F union accepted_shadow(J))
enforced  = F                              # initial shadow rollout
enforced  = closure(F union admitted(J))   # only under a later authorized policy
```

The enforced contract controls collection/readiness/reviewer requirements.
The candidate contract measures what activation would add; it is never dispatched
in shadow mode. Keep the two distinguishable in records and rendering. A recorded
defer about an optional shadow item must not create a new enforced hold.

Mandatory obligations cannot be removed by skip, uncertainty, transport failure,
a high-confidence model answer or a smaller candidate list. Set operations use
stable IDs and deterministic ordering. Deduplicate items while retaining every
valid selection ground. Selection is not approval of executable commands.

## Dependency and contradiction rules

| Item | Closure/validation rule |
| --- | --- |
| Refactor or differential | Include code + refactor + differential and existing frozen owner inputs |
| Registered browser flow | Include browser and the current schema's actual API observations |
| Registered API flow | Include API observations; an offline contract receipt cannot impersonate target API proof |
| Desktop | Include native collector requirement; unsupported native capability remains unresolved |
| Command flow | Require its registered assertions; do not invent a new evidence facet |
| Plan plus current executable changes | Include code; do not require execution of merely planned future behavior |
| Performance criterion | Demand representative measurements; unresolved measurement coverage cannot be called proved by green correctness tests |
| Unknown flow/facet or changed manifest | Reject proposal; preserve its rejection reason without executing it |

An optional candidate whose dependencies are unavailable remains an unresolved
candidate in shadow mode. Mandatory unavailable dependencies are enforced
preparation problems. Any later activation must route an admitted requirement
through stage 4; it cannot silently ignore an inconvenient missing collector.

The current schema has neither a native collector nor admitted representative
measurement coverage. Desktop and performance selections therefore retain an
explicit unresolved item; mandatory selections block preparation, while optional
shadow selections remain audit data. This stage does not supply those collectors
or infer measurement sufficiency from a correctness assertion.

## Per-item provenance schema

The v2 extension, abbreviated here to show its shape:

```json
{
  "version": 2,
  "enforced": {"criteria": ["code", "async"], "evidence": ["offline"], "flows": []},
  "candidate": {"criteria": ["code", "async"], "evidence": ["api", "offline"], "flows": []},
  "items": [
    {
      "id": "criteria:async",
      "membership": ["enforced", "candidate"],
      "grounds": [{"origin": "spec", "scope": "enforced", "rule_id": "F05", "locator": "review.criteria", "input_digest": "<spec-signature>"}]
    },
    {
      "id": "evidence:api",
      "membership": ["candidate"],
      "grounds": [{"origin": "jev-shadow", "scope": "candidate", "request_id": "<id>", "basis_refs": ["<validated-ref>"]}]
    }
  ],
  "unresolved": [{"item_id": "evidence:api", "scope": "candidate", "reason": "no registered API flow"}]
}
```

The example is illustrative, not a runnable spec. Keep existing head/base,
spec signature, manifest/frozen/rubric/catalog identities, required problems
and round references. Each item may carry multiple grounds:

| Origin | Required inspectable ground |
| --- | --- |
| Profile | Asked/effective profile, artifact root, widening rule |
| Spec | Requirement/declaration locator and current spec identity |
| Diff | Changed path or unknown-impact reason; head/base/diff identity |
| Refactor | Run/step, baseline, frozen digest and protected paths |
| Manifest | Registered flow/assertion ID, manifest digest, impact match or full-catalog reason |
| Dependency | Parent item IDs and versioned closure rule |
| Jev shadow/active | Request/version, candidate ID, validated source references and disposition |

Grounds explain why an item was included; they do not establish that its receipt
passes. Never treat a model-proposed citation as authoritative unless it resolves
to offered context. An unsupported suggestion stays labeled unsupported.
Record rejected, stale and uncertain proposals with reasons; do not invent a
ground for a legacy record which never had one.

## Identity and persistence

Use separate execution and audit identities. The enforced digest binds spec,
head/base, deterministic rule versions, selected flows and requirements, manifest,
frozen inputs and enforced rubric content. Receipt identity remains separate until
collection and is bound at review dispatch/approval.

Shadow answers, probability variation and candidate-only additions do not change
the enforced digest or invalidate an otherwise valid approval. Their request,
context and candidate digests bind the audit observation. Changes to mandatory
inputs, applied policy, enforced flow or executed evidence invalidate review.

Construct a contract from one input snapshot. Re-read the identity before
persisting or dispatching; reject stale results instead of assigning them to the
next spec revision. Use the existing spec owner/lock and round store, not direct
edits to raw metadata. On restart, recompute current obligations; reuse a record
only under matching identities and the existing readiness rules.

`store()` recomputes the mandatory contract under the spec owner's lock before
publication and dispatch. It refuses a changed spec birth/revision, head/base,
manifest, frozen inputs, rubric/catalog or superseded attempt. Optional catalog
changes mark only the audit observation stale. The offered flow IDs, kinds,
impact paths, environments and assertion IDs must still match the registered
catalog. Observation completion also rechecks frozen/rubric/catalog digests within
the shared shadow budget. Round publication shares the same lock with its final
snapshot check; a revision made while the reviewer runs produces an uncounted
stale round rather than approval of the replacement requirements.

## Rendering and diagnostics

Extend the existing composed-contract instruction section. Render enforced
criteria, required evidence and registered assertions first. Provide concise
grounds per item, then any unresolved enforced preparation problem. Shadow
candidates appear only as observational diagnostics, clearly not requirements
the reviewer may use to authorize new tools.

Expose the distinction through existing spec/view records before adding bespoke
screens. Display the originating requirement/path/flow and the freshness reason
for a hold. UI labels remain Korean under repository conventions; stored agent
contracts and blueprint prose are English. Redact values through existing
verification helpers before logs, instructions or publication.

## Verification and completion

Add focused cases to `tool/test_review_contract.py` for duplicate/multiple grounds,
deterministic ordering, closure, unknown candidates, source-reference rejection,
mandatory-floor inclusion and audit-only recommendations. Verify:

- Every enforced and candidate item has at least one inspectable ground; derived
  items point to an existing parent and closure rule.
- On/off, probability variation and an optional defer leave execution identical
  in shadow mode. No collector or reviewer is dispatched solely by a candidate.
- A mandatory requirement with no registered coverage remains unresolved.
- Head/spec/manifest/frozen/rubric changes invalidate enforced identity; candidate
  changes alone do not. Stale observations cannot overwrite a new attempt.
- V1 and legacy records remain readable with explicitly unavailable provenance.

Stage 3 completes when both compositions, grounds, rejection dispositions and
identity behavior are implemented and replayable. It does not require enabling
Jev recommendations. Before any later activation, freeze a policy, use independently
reviewed validation families, declare acceptable omission/extra-work limits and
obtain the explicit rollout decision. Historical synthetic scores are insufficient.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Existing union | Deterministic obligations and aggregate digest | Done |
| 2 | Ground each item | Versioned origin ledger and validated source references | Done — scoped v2 origins, stable item IDs, multiple grounds and offered-reference validation |
| 3 | Candidate composition | Audit-only dependency closure, rejections and diagnostics | Done — replayed choices, dependency closure, separate audit identity and unresolved scopes |
| 4 | Verify | Identity, replay and legacy migration checks | Done — integration and final focused regressions passed on 2026-10-07 |

## Implementation verification

Recorded on 2026-10-07 against this implementation working tree. The broader
invocation started before the final catalog-validation, observation-copy and
missing-version hardening; focused reruns cover those subsequent changes.
These invocations overlap and are not a unique-test sum.

| Check | Result |
| --- | --- |
| `test_review_contract.py`, `test_loop.py`, `test_local_verification.py`, `test_specs.py`, `test_review_routing.py` | 285 passed; temporary Git, controlled model/reviewer/GitHub replies and isolated HTTP API |
| Final catalog/closure/identity/legacy selection in `test_review_contract.py` | 18 passed; invalid catalogs, audit-only flow dispatch, stale snapshots and legacy rendering |
| Observation-copy and malformed-catalog rerun | 4 passed; independent frozen input snapshot and command/assertion/kind rejection |
| Final legacy rerun | 2 passed; digest equality without a schema version cannot reuse v2 approval |
| `test_agent_decisions.py`, `test_review_routing.py` | 32 passed; existing decision ownership and separate-process replay remain compatible |
| Ruff, wiki lint, diff whitespace and UTF-8 without BOM | Passed |
| Repository debt gate | Failed only on five existing findings in `chat_session.py`, `work.py`, `test_agent.py`, `test_loop.py` and `desktop_browser.py`; no limit was widened |

Candidate flow additions never called the collector or widened reviewer tools.
An actual registered mandatory API flow still used its existing evidence owner.
Spec revisions during review produced a stale round followed by review of the
current revision. Off/on, probability variation, optional defer and unsupported
grounds preserved the execution contract. Native and representative measurement
coverage remained unresolved rather than receiving substituted proof.

No paid Jev request, human semantic evaluation, actual independent reviewer or
native-host interaction was part of these checks. Collector implementation remains
stage 4 work; this completion does not activate recommendations.

## Rollback

Return to enforced deterministic floors without deleting audit records. A schema
rollback must not reuse a richer approval under a weaker contract: require fresh
review when an obligation-bearing record cannot be validated. Preserve v1 legacy
compatibility only within the bounded rules documented by stage 1.
