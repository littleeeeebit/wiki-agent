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

Limits are 15 seconds shared by preparation/normalization/decision/identity checks, one Jev call,
eight retrieved chunks, 24,000 context characters, 32 grounds and 16 catalog flows.
Oversized context produces unavailable advice, never a weaker baseline. Context
size is checked again after normalization, before building the request.
Manifest tracked-file queries and final Git/GitHub identity checks use the remaining
shared deadline. Final identity refreshes the base and compares both its tip and
merge base, so an independently advanced base is stale even if the merge base
did not move. A failed refresh records unavailable advice, not an endless stale retry.
Local retrieval uses at most two seconds, stops its socket worker on cancellation and
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
| 4 | Evaluate | Human-reviewed labels and production-prompt validation | In progress — on Claude-labelled real tasks, shadow-3 reached 0.95 held-out accuracy and 0.97 required recall (below); human-reviewed labels and a fresh set of tasks remain |

## Observed in production (2026-11-04)

Spec records from 170 real tasks across six repositories held about 2,600 shadow
observations. Only 2 were `decided`, and 187 were `uncertain` with answers. The
rest sent no request:

| Outcome | Oct | Nov 1–4 |
| --- | ---: | ---: |
| `context_too_large` | 692 | 200 |
| `normalization_failed` | 211 | 173 |
| `stale` | 232 | 0 |
| `uncertain` (answered) | 152 | 35 |
| `decided` | 2 | 0 |

Two causes:

- Size. The eight retrieved passages (1,600 characters each) and the 6,000-character
  diff alone came near the 24,000-character ceiling, and the goal and done items
  appear twice: in the state and as grounds. Anything over sent nothing.
  `review_contract.fitted` now drops the lowest-ranked retrieved passages, then
  shortens the diff. It runs before and after normalization, and records what it cut
  in `observations.fit`. The ceiling itself is unchanged.
- Normalization was all or nothing. In one real task, 13 of 14 strings were English
  or translated, and the last came back `uncertain` (`not_english`). That made the
  whole context unavailable. `decisions.normalized(mask=True)` now sends such a
  string with its Hangul masked, as a path is sent, while at most half of the
  strings fail. Other callers keep the refusal. Jev still receives English only.

## Claude-labelled evaluation (2026-11-04)

The 55 tasks whose records held a sent request gave one case each: the latest
request. `tool/eval/review_shadow_labels.py` had Claude Opus 5.5 label every
offered candidate from the same English state Jev was given, never from Jev's
answers: `required`, `acceptable`, `wrong` or `unresolved`. These labels are
Claude's, not human-reviewed. Families split 30 calibration and 25 held-out, by
a hash of the task id. Each labelling run cost about $3.

The measure, agreed with the owner, has two parts on the held-out families.
Accuracy counts a candidate as right when a `required` one is added, a `wrong`
or `unresolved` one is not, and an `acceptable` one either way. Required recall
is the share of `required` candidates that were added. Both must reach 0.9.
Answering "not needed" to every candidate already scores 0.82 accuracy.

Jev with the shadow-2 questions, on labels made against them:

| Held-out | Accuracy | Required recall |
| --- | ---: | ---: |
| Frozen production answers | 0.83 | 1/38 |
| Live, 0.6 confidence bar | 0.83 | 1/38 |
| Live, Jev's top choice | 0.79 | 21/38 |

Jev was not choosing badly so much as not choosing: the median confidence was
0.2–0.3, and almost no answer cleared 0.6. Its probability of adding still
ranked required candidates above wrong ones (AUC 0.80 on both splits). The
cause was the question. Every candidate offered the same pair, "Additional
attention is needed" / "No additional attention is needed", after a paragraph
of prohibitions. The synthetic experiment that worked gave each option its own
condition. Jev's documentation says the same: wording and irrelevant context
change its answers.

The changes, all fitted on calibration families only:

- Shadow-3 questions. Each candidate asks whether that facet is required, with
  its own "required when" and "not required when" conditions. The conditions
  live in `tool/prompts/review-shadow.json`; the labeller shows Claude the same
  ones. A registered flow is named by id, not copied into every question.
- `fit_body`. The state is fitted beside its questions inside Jev's 100 kB
  request body, counted in UTF-8 bytes. The body is measured again after each
  passage or diff cut, since the basis questions list every ground. A task with
  fifteen registered flows had sent nothing (`state_too_large`).
- `add_at = 0.25`, a rule of the recorded policy. A candidate is added unless
  Jev puts more than three quarters on not adding it. Each record replays under
  its own policy, so a record frozen under shadow-2 replays as it was decided.

On calibration, the questions alone gave 0.92 accuracy and 0.77 recall at the
0.6 bar. Lowering the bar was the larger lever: 0.94 and 0.96. Sharpening
the weak conditions (documentation and plans, security, data, api, desktop,
differential) moved little. The conditions, the threshold and a third set of
labels were then frozen, and the held-out families were run once:

| Held-out, shadow-3, `add_at` | First run | After review fixes | 95% Wilson lower bound (after) |
| --- | ---: | ---: | ---: |
| Accuracy | 216/223 = 0.969 | 212/223 = 0.951 | 0.914 |
| Required recall | 59/60 = 0.983 | 59/61 = 0.967 | 0.888 |
| Wrong candidates added | 5 | 7 | — |

Both runs meet the agreed 0.9 point targets; the recall lower bound does not,
with 61 required candidates. The second run followed the round 1 fixes (the
body fitted in UTF-8 bytes, measured again after each cut; the threshold as a
rule of the recorded policy). It also followed one label change. The reviewer
spot-checked 15 held-out labels and agreed with 14. One `criteria:performance`
became required, because acceptance set a one-minute trace latency budget. The
fits left every labelled state as it was. The two runs differ by that label
and by Jev's run-to-run variation. Calibration in the second run: accuracy
0.940, recall 87/92.

Limits: Claude wrote the labels and the conditions both, so this measures how
well Jev applies Claude's reading of the conditions. One label set is
correlated with the conditions it was made under. Each task gave one case, and
the 55 come from six repositories. Before anything is enabled, human-reviewed
labels on real advice and a fresh set of tasks remain the gate.

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

## Independent review repair

ORCA's independent 5.6 sol reviewer inspected PR #92 at `334663f` and reported
three P1 findings: normalization could expand past the context ceiling, the
base-tip identity was not refreshed, and manifest Git queries used independent
60-second limits. The reviewer ran 94 focused checks successfully; that did not
establish the missing boundary guarantees.

The repair rechecks normalized context before request creation, compares refreshed
base tip and merge base, and passes the same remaining budget through manifest
queries and terminal identity checks. Existing helper callers retain their defaults.
Seven focused regression cases passed, including a real sleeping subprocess
reaped at its half-second manifest deadline. The broader contract/routing/local
verification/spec invocation finished with 121 passed and two failures from a
test double that omitted production call-budget accounting. After correcting
that double, both failed cases passed in a focused rerun. These overlapping runs
are not a unique-test sum or a claim that the broad invocation was green.

Ruff, wiki lint, diff whitespace and UTF-8 without BOM passed. The debt gate
still reports only the five pre-existing findings listed above. Independent
review of the repaired head and the final gate remain separate checks.

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
python tool/eval/review_shadow_labels.py --out raw/eval/review-shadow-golden.json
python tool/eval/review_shadow.py --dataset raw/eval/review-shadow-golden.json --live --rebuild --out raw/eval/review-shadow-live.json
```

`--rebuild` asks each frozen state with the current questions, fitted as
production fits it, instead of refusing a record made under an earlier prompt.

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
