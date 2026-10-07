# Stage 2 — contextual Jev recommendations in shadow mode

See the [overview](0-overview.md). Prerequisite:
[required obligations](1-required-obligations.md). Output feeds
[composition and provenance](3-composition-provenance.md).

Purpose. Detect context-specific review attention that explicit metadata misses,
without allowing the initial recommendation to change execution, readiness,
permissions, approval or merge eligibility.

## Existing implementation and remaining work

The v2 implementation in `tool/main/review_contract.py` prepares spec acceptance
locators, a bounded actual diff, a validated flow catalog, scoped wiki/decision
passages and receipt identities. `main/knowledge.py::prepare(context_only=True)`
reuses the existing repository-scoped retrieval contracts without model routing,
external acquisition, daemon startup or a detached cold-index worker. The running
index is optional: its absence is recorded separately from normalization failure.
Retrieval is discovery, not an endorsement of the passage's instructions.

`shadow()` uses one typed decision call with closed add/skip/defer questions for
additional facets and registered flow IDs. A separate closed question selects one
offered ground per recommendation. Missing or uncertain grounds remain unsupported
audit data. An offered reference is a citation, not proof that the model's causal
explanation is correct. Unknown answer/flow/ground IDs fail typed validation.
Commands from the manifest are excluded from the offered catalog.

`observe_shadow()` runs after deterministic selection and before contract
readiness and reviewer-cell creation. It saves preparation and terminal attempts
through the spec owner outside the checkout. An interrupted preparation becomes
an aborted observation when the next attempt starts. Spec revisions, checkout/PR
head, PR base and manifest changes invalidate an applicable result. The baseline
contract digest, enforced obligations, collection and dispatch remain unchanged.
Earlier synchronization/gate/Cloud failures still belong to their existing owners;
they do not produce a contextual request before a safe diff is available.

Limits are 15 seconds shared by preparation/normalization/decision, one Jev call,
eight retrieved chunks, 24,000 context characters, 32 grounds and 16 catalog flows.
Oversized context produces unavailable advice, never a weaker baseline. Local
retrieval uses at most two seconds, stops its socket worker on cancellation and
does not replace an incompatible running daemon. Owned normalization processes
batches on the caller's thread, checks cancellation between batches and uses the
remaining deadline; an in-flight synchronous translation finishes before the
operation returns. Private context is not written into the shared translation
cache. Transport and provider behavior still require live acceptance evidence.

`replay_shadow()` validates a frozen request/result and reuses the recorded policy
without normalization, retrieval or transport. `tool/eval/review_shadow.py` replays
spec observations or evaluates a frozen labelled dataset; only explicit `--live`
sends requests. Labels/rationales never enter a request. A live run must match the
current production questions, prompt, catalog and policy. Model-authored historical
labels have not become human-reviewed labels, and no new live semantic validation
is claimed by these implementation checks.

## Context preparation blueprint

Reuse `main/knowledge.py::prepare` and existing source/decision contracts; do not
add a separate search engine or a free-running agent. Build a round-owned context
from the following sources, using one approved repository scope and a bounded
deadline shared with normalization and decision work.

| Source | Context needed | Ground retained |
| --- | --- | --- |
| Spec | Requirement IDs, observable acceptance, exclusions and implementation origin | Spec ID/revision, content signature, acceptance locator |
| Diff | Actual changed paths and relevant caller/contract boundaries | Head/base, file and stable locator/content hash |
| Registered flows | Available IDs, kinds, assertions, impact and environment dimensions | Manifest digest and flow/assertion IDs |
| Wiki/decisions | Prior incidents or adopted constraints relevant to this task | Repository/source revision, chunk ID and original locator |
| Existing observations | Missing prerequisites and known evidence coverage | Receipt identities; no secrets or unbounded logs |

Retrieval discovers candidate evidence; it cannot register an execution command
or turn prose into mandatory policy. Cross-repository content requires the
existing explicit scope relationship. Preserve original locators when normalizing
to English, and redact configured private values before any external request.
Unavailable retrieval and unavailable translation are distinct trace outcomes.

Keep context size and budgets explicit in the versioned request. Current shadow
uses one decision call and a 15-second `Budget`; preparation must fit the total
operation budget rather than independently multiplying calls/time. If no safe
context is available, retain deterministic behavior and record why advice is absent.

## Recommendation contract

Code offers candidates; Jev chooses among them. Facet candidates use stable IDs
such as `criteria:async` or `evidence:api`. Add registered `flow:<id>` candidates
only after the manifest is validated. Each question carries the candidate's
meaning and sufficient contextual grounds; an opaque ID alone has no semantics.

Allowed answers are `add`, `skip` and `defer`. They mean additional attention,
not review readiness. Unknown IDs, commands, tool names, invented URLs or new
environment scopes fail validation. Do not ask Jev to certify receipt identity,
grant permissions, waive a floor or issue a review verdict.

Proposed extension to the existing shadow record:

```json
{
  "schema_version": 2,
  "mode": "shadow",
  "input_identity": "<deterministic-input-digest>",
  "context_digest": "<scoped-context-digest>",
  "context_refs": ["<source-id:revision:locator>"],
  "recommendations": [
    {"candidate_id": "criteria:async", "choice": "add", "basis_refs": ["<offered-ref>"]}
  ],
  "prompt_version": "<version>",
  "policy_version": "<version>",
  "status": "decided"
}
```

This is a target schema, not a claim about v1 fields. Preserve the existing
request ID, model, typed answers/verdicts, probabilities, normalization version,
usage, elapsed time, budget and failure reason. Referenced grounds must come from
offered context. If Jev cannot return trustworthy grounds, retain the recommendation
as unsupported audit data; do not manufacture a causal explanation from confidence.

## Placement and lifecycle

Target order is deterministic selection, context preparation, shadow advice,
candidate composition, then readiness/collection. This lets missing preparation
be observed without granting execution. Moving the existing call earlier must
retain the same review authorization boundary and must not create a reviewer
cell merely to ask Jev.

Each operation belongs to one spec/round attempt. Cancellation reaches retrieval,
normalization and transport. No detached shadow thread or unowned process may
survive the operation. Re-read spec/head before recording an applicable result;
changed identity marks it stale, and it cannot attach to the replacement round.
Persist aborted/preparation observations through the existing spec owner, outside
the source checkout, so a restart does not fabricate a successful decision.

| Outcome | Enforced effect | Recorded effect |
| --- | --- | --- |
| Off or already cancelled | Baseline only; no request | Not asked |
| Valid add | Baseline unchanged | Candidate addition with grounds |
| Skip | Baseline unchanged | Offered candidate not recommended |
| Defer/low confidence | Baseline unchanged | Specific unresolved candidate/question |
| Retrieval/normalization/provider failure | Baseline unchanged | Distinct unavailable reason |
| Budget exhausted | Baseline unchanged | Exhaustion and consumed budget |
| Stale input | No recommendation application | Stale identities retained |

The current `0.6` confidence and `0.2` margin policy is provisional shadow
configuration, not a demonstrated safe production threshold.

## Evaluation dataset and replay

Reuse the [research dataset](../../../eval/jev/review-routing.json),
[fresh synthetic validation](../../../eval/jev/review-routing-validation.json)
and [results](../../../eval/jev/review-routing-results.json) as versioned evidence.
The existing 48 development and 32 fresh validation situations are model-authored
and not human-reviewed. The evaluated synthetic v3 prompt is different from
`review-contract-shadow-1`; its accuracy does not certify the production prompt.
Do not repeat the 240 historical paid requests merely to write or lint this plan.

For the next evaluation version, keep whole scenario families together, freeze
validation before inspecting answers and keep labels/rationales out of requests.
Each situation/answer pair must specify required criteria, evidence, registered
flows, prohibited substitutions, expected unresolved inputs and acceptable extras.
Add human-reviewed real task disagreements and adversarial incomplete manifests.

| Family | Required distinction |
| --- | --- |
| Plan versus implementation | Future acceptance design versus evidence of an implemented operation |
| Refactor versus feature/bug fix | Behavior preservation versus intentionally changed behavior |
| API contract versus deployed dependency | Local executable contract sufficiency versus required target-environment observations |
| Browser versus native host | DOM/API proof does not prove actual app/window/hook events |
| Data/security/async/performance | Migration/loss, trust boundary, lifecycle and representative measurement obligations |
| Ambiguous impact or unavailable setup | Specific uncertainty; no invented receipt or offline substitution |

Report required-item omissions, unnecessary additions, exact facet/flow sets,
needless holds, disagreement with baseline, option-order/renaming sensitivity,
latency, tokens and known cost separately. Readiness is a code result, not Jev's
answer. Evaluate a fixed request/prompt/context/catalog version; one changed
component invalidates a claim that the old scores describe the new policy.

## Verification and exit

Use `tool/test_review_contract.py` for shadow non-interference, off/missing-key,
cancel and failure behavior; `tool/test_review_routing.py` for dataset isolation
and option mappings. Add focused checks for scoped retrieval, injected instructions,
unknown flow/ground references, cancellation during preparation, stale results
and replay without transport. A live semantic experiment must preserve its frozen
inputs and report whether it used synthetic or actual project context.

Stage 2 is complete when contextual facet/flow recommendations are bounded,
grounded, persistently replayable and evaluated with the exact production prompt,
while shadow on/off leaves enforced obligations and dispatch unchanged.
Active mode is not required to complete this stage and is not enabled by it.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Baseline shadow | Closed facet advice, bounded call and non-interference | Done — current focused suite |
| 2 | Ground context | Scoped retrieval and registered flow candidates | Done — bounded v2 context and closed flow/ground questions implemented |
| 3 | Persist lifecycle | Preparation/stale observations, ground refs and replay | Done — spec-owned attempts, identity recheck, unsupported grounds and offline replay implemented; live transport acceptance remains part of evaluation |
| 4 | Evaluate | Human-reviewed labels and production-prompt validation | In progress — exact production-request replay/evaluation harness implemented; human-reviewed real cases and live validation remain |

## Implementation verification

Recorded on 2026-10-07 against the implementation working tree. These invocations
overlap; their counts must not be summed into a unique-test total.

| Check | Result |
| --- | --- |
| `test_review_contract.py`, `test_agent_decisions.py`, `test_loop.py`, `test_local_verification.py` | 238 passed; controlled reviewer/GitHub/model replies with real temporary Git and isolated HTTP API |
| `test_review_contract.py`, `test_review_routing.py`, `test_search.py` | 55 passed; includes shadow on/off dispatch equality, exhaustion, scoped references, cancellation, invalid catalogs and replay |
| `test_review_routing.py`, `test_translate.py`, `test_search.py` | 66 passed; owned socket/batch behavior and production-request schema checks |
| `test_review_routing.py`, `test_specs.py` | 33 passed; separate-process offline CLI replay, frozen family/label isolation and spec/profile compatibility |
| Focused retrieved-context accounting regression | 1 passed; all three discovered chunks charged, including rejected scope/instruction passages |
| Ruff, wiki lint and `git diff --check` | Passed |
| Repository debt gate | Failed on five pre-existing HEAD findings in `chat_session.py`, `work.py`, `test_agent.py`, `test_loop.py` and `desktop_browser.py`; unrelated limits were not widened |

The old controller fixture counted shadow discovery as correction retrieval;
its observations now distinguish those operations. A separate-process CLI check
also exposed an eager profile-helper alias in the spec import cycle; dispatch now
resolves that helper at call time. No new paid Jev request, human label review,
actual independent reviewer or native-host interaction is represented by these
checks. Stage 2 remains in progress until its semantic evaluation exit is met.

## Rollback

Set `WIKI_REVIEW_SHADOW=off` in the Jev environment file or process environment to
disable this integration independently of global Jev agent decisions. An invalid
value sends no request. Retain
records and deterministic floors. Missing keys/provider outages must not trigger
a second model, another agent, a wider permission set or a readiness bypass.

## Replay and evaluation commands

```powershell
python tool/eval/review_shadow.py --spec raw/specs/<repo>/<task>.json --out raw/eval/review-shadow-replay.json
python tool/eval/review_shadow.py --dataset <frozen-labelled-dataset>.json --out raw/eval/review-shadow-evaluation.json
```

The dataset schema is `review-shadow-evaluation/1`. Each case has `id`, `family`,
`split` (`calibration` or `heldout`) and an unchanged v2 `record` containing its
frozen request/result. Optional `expected` lists `criteria`, `evidence`, `flows`,
`unresolved` candidate IDs, `prohibited` candidate IDs and `acceptable_extras`.
Keep label author/reviewer status in top-level `labels`; family membership may
not cross splits. Scores distinguish missing/extra facet and flow items, exact
sets, prohibited substitutions and unresolved inputs. Ground support, baseline
disagreement and typed decision telemetry remain inspectable. These scores are
audit comparisons, not readiness or approval. Add `--live` only for an authorized
new semantic experiment; it does not dispatch a reviewer or execution command.
