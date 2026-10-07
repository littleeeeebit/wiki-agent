# Selecting review criteria and verification evidence

Recommendation. Keep the existing review loop and compose its criteria and
evidence obligations from the current specification, actual diff and registered
verification flows. Use Jev to recommend additional applicable facets where
those facts leave a semantic question. Code must preserve mandatory obligations,
validate evidence identity and retain authorization. The experiment below does
not justify letting Jev decide review readiness on its own.

Research checked on 2026-10-07. The synthetic experiment remains separate from
production. The first production increment is recorded below; this is not a
new shared rule or approval for autonomous semantic routing.

The [four-stage implementation blueprint](../plans/review-routing/0-overview.md)
records the current completion boundaries and specifies the remaining contextual
recommendations, selection provenance and local/native evidence collection.

## Baseline before this increment

| Path | Current behavior | Remaining gap |
| --- | --- | --- |
| [`loop.effective` and `profiled`](../../tool/main/loop.py) | One loop with `plan`, `code` and `mixed` criteria; plan changes outside their Markdown artifact root widen to mixed, including an unreadable changed-file set | No general selection of preservation, prose or runtime evidence from acceptance |
| [`specs.profile_of` and `profiled`](../../tool/main/specs.py) | Missing profile defaults to code; specifications name their profile | File suffix or a task title alone cannot identify the intended contract |
| [`refactor.spec_for`](../../tool/main/refactor.py) | Refactoring creates a code-profile specification and enters the existing authorized loop | The independent review rubric does not explicitly ask preservation and debt questions |
| [`refactor_profile.prepare`, `task` and `drive`](../../tool/refactor_profile.py) | Characterization tests pass before freezing; candidates preserve frozen tests and satisfy the ratchet; selection rewards debt reduction | This is a real refactoring-specific execution pipeline, despite its generic independent review rubric |
| [`refactor_finish`](../../tool/main/refactor_finish.py) | Test cleanup preserves AST/decoded values or measured test/mutant verdicts; later finishing-review changes invalidate its verification | A generic green suite is not the whole preservation contract |
| [`verification`](../../tool/main/verification.py) and [local verification](../local-verification.md) | Cloud implementations require repository flows, API/browser observations and head/environment-bound receipts; missing setup blocks; stale evidence invalidates | Ordinary local and other-environment tasks do not automatically receive this Cloud execution path |
| [`decisions.extra_check` and `fix_turn`](../../tool/main/decisions.py) | Jev can select one additional registered check or gather correction context; required gates remain code-owned | These choices do not select a complete review/evidence contract |

Thus, the loops share orchestration, but neither every prompt nor every
verification pipeline is identical. The existing
[review-profile decision](../plans/reliability/6-review-profiles.md) already
chose a common engine with different criteria. Extend that decision rather than
introduce another loop engine. Current host and PR ownership remains governed by
the [native boundary investigation](native-host-boundary.md).

## Established solutions and their limits

These sources support individual design elements. Their combination below is
our proposal; none is a ready-made implementation of this repository's router.

| Primary source | Finding | Adoption and limit |
| --- | --- | --- |
| [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | Routing permits specialized prompts; evaluator/optimizer works when evaluation criteria are clear | Compose criteria before evaluation; reuse the loop rather than add a framework |
| [LangGraph workflows](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | Structured routing selects a bounded downstream operation | A closed selection is useful; LangGraph itself is unnecessary here |
| [Fowler, Definition of refactoring](https://martinfowler.com/bliki/DefinitionOfRefactoring.html) | Refactoring preserves observable behavior while changing internal structure | Preservation is an extra contract, not an interchangeable name for fixing a bug |
| [Pact provider verification](https://docs.pact.io/provider) | Provider contracts can be verified against a locally running provider with downstream dependencies stubbed | An API-related change need not always hit a deployed API; contract evidence has a specific boundary |
| [Pact can-i-deploy](https://docs.pact.io/pact_broker/can_i_deploy) | Compatibility depends on the tested consumer/provider versions and target environment | Bind evidence to identities; a previous green result is not universal approval |
| [Google SRE, Canarying releases](https://sre.google/workbook/canarying-releases/) | Artificial tests can miss actual state and traffic behavior | Select live evidence when acceptance depends on that environment. This research does not authorize production traffic experiments |
| [LangSmith evaluation types](https://docs.langchain.com/langsmith/evaluation-types) | Curated inputs/reference outputs can evaluate expected tool calls and compare versions | Score the selected criteria, evidence and next operation, not merely fluent final explanations |
| [AgentEvals source](https://github.com/langchain-ai/agentevals) | Reference trajectories can be compared strictly or by inclusion | Required obligations need inclusion checks; unnecessary checks need a separate cost metric. Existing Python comparisons suffice |
| [TypeSafe API](https://docs.typesafe.ai/api) | One state supports independent Choice/Noul/Score questions; question IDs do not supply inference meaning | Put each target and rubric in its question; use existing typed transport and closed IDs |
| [TypeSafe confidence](https://docs.typesafe.ai/confidence) and [Jev jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) | Confidence and probability differ; task wording and irrelevant context affect behavior | Record uncertainty and evaluate local tasks. Concentrated probabilities are not evidence that a receipt is valid |
| [Sun et al., option-name sensitivity, v2](https://arxiv.org/abs/2609.26758v2) | Changing the assignment of option names to rubrics can change decisions despite type-correct outputs | Test neutral option renaming and ordering. This preprint does not establish this application's error rate |
| [Deußer et al., Jev benchmarking, v1](https://arxiv.org/abs/2609.37647v1) | Performance and useful thresholds vary by dataset and question form | Broad benchmark results do not replace a review-selection evaluation |
| [Liu, selective security automation, v3](https://arxiv.org/abs/2609.33401v3) | Overall accuracy can conceal confident errors; independent evaluation must include missed unsafe inputs and false alarms | Measure omissions, premature progression and needless holds separately. Its security-task findings are not a measured review-routing result |

## Proposed contract

Criteria and evidence are separate sets. A refactor can require API evidence;
an implementation can be entirely local; a future API plan needs review of its
acceptance design without pretending that the future API was exercised now.

| Criteria facet | Reviewer question | Typical evidence |
| --- | --- | --- |
| Code | Does implemented behavior meet acceptance without unsafe transitions or regressions? | Diff, applicable execution receipts, repository gate |
| Plan | Can an implementer realize and observe every requirement? | Requirement mapping, sources, dependencies, authority, rollback |
| Refactor, added to code | Are outputs, errors, side effects and caller contracts preserved; is debt actually reduced? | Frozen before/after witnesses, ratchet, scope and test-strength evidence |
| Documentation | Is ordinary prose accurate and are its references valid? | Sources and document checks; executable Markdown retains code criteria |

Evidence facets are `offline`, `api`, `browser`, `desktop` and `differential`.
They compose: a browser saving to a deployed API requires both browser and API
observations. Before/after preservation can also require a real host or service.
A receipt's kind never proves that its observations or test scripts are sound.

The proposed `review-contract/1` record contains repository/specification
identity and revision, head and merge base, changed-file digest, requirement IDs,
criteria IDs, required registered flow/check IDs, environment revisions,
selection provenance, rubric/prompt/policy versions, and unresolved inputs.
These are proposed fields, not an existing server schema.

1. Code derives floors from the profile, actual diff, refactoring origin and
   repository-owned impact/verification declarations. Unreadable or unclassified
   impact cannot become a docs-only or offline-only exemption.
2. Retrieve compact relevant specifications, caller boundaries, decision records
   and registered flow descriptions through existing knowledge preparation.
   Keep source locators; retrieved prose is evidence, not executable policy.
3. Offer Jev existing facet/flow IDs. It may propose additional review attention
   or evidence, not arbitrary shell commands, new permissions, waived checks or
   a merge verdict. Known metadata decisions need no model call.
4. Compose required sets by union. Refactor implies code and preservation
   witnesses. Uncertain optional selections need a specific unresolved question;
   uncertainty about an already enforced floor cannot remove that floor.
5. The existing execution owner gathers evidence in its authorized test scope.
   Validate current head/base/spec/environment and applicable assertions before
   handing it to the existing independent reviewer. Missing environment and
   missing evidence are distinct outcomes. The model cannot turn either into a pass.
6. Persist the actual criteria, evidence, rejected suggestions and selection
   basis with each round. A changed identity invalidates the decision. Existing
   affected-flow reuse must retain impact reasons and environment binding.

Review dispatch still needs the existing explicit authorization or previously
authorized refactoring tier. A recommended route does not start a reviewer.
Ordinary reviewers retain read-only permissions and receive receipts from their
execution owner; choosing a live facet does not grant Cloud verification tools.
Existing stale-review, stable finding, final-gate, recurrence and merge rules
remain authoritative. The synthetic research outcome below does not implement
the still-open [recurrence plan](../plans/reliability/8-recovery.md).

## Evaluation design and provenance

The [development dataset](../../eval/jev/review-routing.json) has 48 synthetic
situation/answer pairs in 24 scenario families. Its original split was 16
calibration and 32 heldout cases, with whole families kept together. Prompt v1
performed poorly. V2 clarified rubrics, but its code/ordinary-gate alternative
rubrics still overlapped: the option meaning 'unneeded' included language about
keeping an obligation. That was our experimental prompt defect, not evidence
against Jev's underlying ability. V1 and v2 failures are retained.

After inspecting v2's heldout errors, that original dataset became development
and regression data. We corrected the overlapping alternatives, froze v3 and
the provisional confidence/margin policy (`0.6`/`0.2`), then authored
[32 fresh validation pairs](../../eval/jev/review-routing-validation.json) in
16 new families. No prompts or thresholds were tuned on their returned answers.
Same-family complete/incomplete pairs are correlated, not independent trials.
Both datasets were authored by the implementation model and have no human
label review; the fresh split is not an independent author or production sample.

Each request sends only a synthetic state, excluding gold labels, rationale,
case ID and split. Inputs include acceptance, changed paths, origin/profile,
registered obligations, head/base/spec/environment, synthetic receipts and
recurrence history. Ten independent Choice questions select four criteria,
five evidence facets and one next step. Questions cannot read each other's
answers. Model `jev-1.13.0` uses the existing `decision.request`, `decide` and
`checked`; there is no host fallback or reviewer invocation.

The [runner](../../tool/eval/review_routing.py) measures three candidates:
metadata-only proposed rules; raw Jev choices; and accepted Jev additions plus
code floors and synthetic receipt checks. `current_profile_exact` separately
calls the real `loop.effective`: it measures only today's criteria, not today's
entire Cloud or refactoring pipeline. The proposed rules arm is not the current
production implementation. `review` means ready to request independent review,
never approval or permission to merge.

## Measured result

The [durable evidence record](../../eval/jev/review-routing-results.json)
preserves every experiment's summary, hashes and usage, plus final validation
predictions, probability distributions, option mappings and the frozen prompt.
Full local responses remain at its `raw_path` locators under ignored `raw/eval/jev/`.

| Fresh validation, normal options | Proposed metadata rules | Jev alone | Jev plus floors |
| --- | --- | --- | --- |
| All criteria, evidence and next step match | 14/32 | 20/32 | 26/32 |
| Criteria match exactly | 28/32 | 30/32 | 32/32 |
| Evidence matches exactly | 16/32 | 30/32 | 32/32 |
| Required evidence omitted | 16/32 | 0/32 | 0/32 |
| Unnecessary evidence added | 0/32 | 2/32 | 0/32 |
| Advances to review despite missing prerequisites | 5/32 | 6/32 | 0/32 |

The normal combined candidate forwards 8 of 14 actually ready cases and holds
the other 6. Its 26/32 total match is 81.25%, with all six errors being needless
holds. This is not 100% successful automation. The observed zero premature
advances is not a population guarantee; this sample is small and synthetic.
Safety and progress must be reported together.

| Same validation cases, frozen v3 | Raw exact | Combined exact | Raw choices changed vs normal | Combined selections changed | Ready cases forwarded |
| --- | --- | --- | --- | --- | --- |
| Normal | 20/32 | 26/32 | — | — | 8/14 |
| Identical repeat | 21/32 | 28/32 | 1/32 | 2/32 | 10/14 |
| Reverse option insertion order | 14/32 | 22/32 | 8/32 | 4/32 | 4/14 |
| Rotate neutral IDs among unchanged rubrics | 23/32 | 27/32 | 3/32 | 3/32 | 9/14 |
| Rename and reverse | 18/32 | 21/32 | 8/32 | 5/32 | 4/14 |

Each row contains 32 correlated cases, not 32 new independent examples.
No combined variant omitted a required facet or advanced prematurely, but the
rename-and-reverse variant added one unnecessary preservation obligation.
Option sensitivity exceeds the observed identical-repeat variation. One repeat
does not estimate a stable stochastic floor or establish statistical significance.

There were 240 live synthetic requests across nine retained runs: 587,751
input tokens and 108,960 output tokens. Request medians range from 214.5 to
246.5 ms; this excludes future retrieval and reviewer time. Actual billed cost
is unknown because the transport reports tokens rather than currency. No
credentials, private dataset or real project content was sent by this experiment.

## Adoption decision and next work

Adopt the architectural separation and expand deterministic obligations first.
Do not enable the experimental Jev readiness decision in production. Its needless
holds and sensitivity make it unsuitable as the sole review-route authority.
The smaller first implementation is a refactoring rubric plus registered
verification-flow selection inside the existing loop. Semantic Jev additions
should initially be shadow recommendations with recorded disagreements.

| Stage | Existing owners to extend | Required evidence before enabling |
| --- | --- | --- |
| Explicit facets | `specs.profiled`, `loop.effective`/`profiled`, refactoring spec origin | Current plan/mixed guards retained; refactor intent separated from bug fixes; executable Markdown cannot bypass code |
| Required verification flows | `verification` receipt/impact helpers and `specs` registered checks | Local, external and Cloud ownership preserved; missing API/host prerequisites stay pending; stale identities never authorize review |
| Semantic recommendations | `main.decisions` and `knowledge.prepare` | Human-reviewed labels, naturally occurring task cases, compact evidence grounded in repository declarations; subset errors and unnecessary work recorded |
| Limited activation | Existing owner dispatch and diagnostics | Untouched evaluation families, measured coverage and error limits, outage/cancel/stale handling and actual per-environment flow runs; explicit product authorization retained |

Add counterexamples from real task disagreements to development data. Freeze a
new validation set before changing a prompt, threshold, context selection or
catalog. Include incomplete/unclassified manifests, multiple live dependencies,
shared-file impact, renames, approval removal, provider failure and observed
flakiness. Choose acceptable miss and needless-hold limits explicitly before
active rollout; this session neither invents them nor fits a production policy.
Rollback should disable semantic recommendations while retaining explicit
criteria, mandatory flows and old round records.

## First production increment

[`review_contract.py`](../../tool/main/review_contract.py) composes obligations
inside the existing loop. It reuses the existing profile rubrics and verification
manifest/receipt identity checks, rather than introducing another loop or executor.

| Task | Criteria composition | Evidence floor |
| --- | --- | --- |
| Ordinary implementation | Code; optional security, data, async or performance attention | Current-head round checks and final full gate; runtime flows explicitly declared by acceptance |
| Behavior-preserving refactoring stage | Code + refactor | Frozen characterization command rerun for round and final checks; protected test paths unchanged from the stage baseline |
| Characterization, test cleanup or ratchet finishing stage | Code; existing stage owner retains its own evidence checks | Existing characterization/cleanup/ratchet proofs; no circular demand to preserve newly authored tests |
| Plan within its ordinary Markdown artifact root | Plan | Requirements and source manifest; future runtime acceptance is reviewed, not executed as if implemented |
| Plan plus implementation/executable Markdown | Plan + code | Both rubrics; applicable current implementation checks |
| Legacy prose-only change | Code + documentation | Conservative code floor retained, with factual/source/reference review |
| Declared API/browser acceptance | Existing profile + declared lenses | Registered isolated flow receipts; browser flows also supply actual API requests under the existing receipt schema |
| Declared desktop acceptance | Existing profile + declared lenses | Preparation wait: no native-host receipt validator is installed yet |

Specs can declare `review.criteria`, `review.evidence` and `review.flows`; only
closed facet names and registered flow IDs are accepted. Spec generation and work
prompts instruct models to derive declarations from observable acceptance, not
task keywords. Declarations add to floors and never carry executable commands.
Actual diff paths can widen runtime-flow selection; shared, unreadable or partly
unmapped impact selects all registered flows. Cloud retains its complete major-flow
checklist and existing repository-declared prose exemption.

Each round stores its composed contract, head/base, spec signature, rubric/catalog
digest, manifest/frozen specification digest and Jev shadow observation. A changed
contract or runtime receipt invalidates approval at completion, screen and merge
boundaries. Read-only ordinary reviewer permissions remain unchanged. Legacy ordinary
approvals without a new contract remain compatible; adding explicit obligations or
a refactoring origin requires a fresh review.

The frozen specification's SHA-256 is also an argument to its preservation
command. Thus changing it invalidates both targeted-check reuse and final-gate
identity, not just the review prompt; the runner checks the digest before dispatch.
Registered runtime observations are supplied to ordinary read-only reviewers with
configured private values redacted. Missing setup, missing receipts and changed
execution identity remain distinct preparation reasons.

The production shadow asks only about additional facets, never readiness. One
bounded call belongs to the round rather than a detached thread; mode off sends
nothing. Outage, uncertainty or cancellation cannot waive a floor. Answers,
probabilities, policy, question digest and usage are kept beside the round for
future evaluation. This is a new prompt, not the evaluated synthetic v3 prompt;
the research scores are not its production accuracy. It uses compact task/diff
metadata and selected flow descriptions, not yet semantic retrieval of caller
contracts or a human-reviewed evaluation set.

Ordinary local runtime collection is deliberately not automated by this increment.
The validator can accept receipts produced by the existing approved executor;
otherwise it waits for that owner. It never upgrades an offline check into live
proof or grants Cloud tools because Jev selected a facet. A local-flow execution
owner, native-host collector and human-reviewed semantic activation remain follow-up
work. Performance attention requests measured before/after evidence from the
reviewer; there is not yet a general benchmark-receipt validator.

`tool/test_review_contract.py` exercises the production dispatch and approval
boundaries with temporary Git repositories and an actual isolated local HTTP API.
No production service, private dataset or actual reviewer is used by these tests.

## Reproduction

```powershell
python -m pytest -q tool/test_review_contract.py
python -m pytest -q tool/test_review_routing.py
python tool/eval/review_routing.py
python tool/eval/review_routing.py --dataset eval/jev/review-routing-validation.json
python tool/eval/review_routing.py --dataset eval/jev/review-routing-validation.json --live --split heldout
python tool/eval/review_routing.py --dataset eval/jev/review-routing-validation.json --live --split heldout --variant reverse
python tool/eval/review_routing.py --dataset eval/jev/review-routing-validation.json --live --split heldout --variant rename
python tool/eval/review_routing.py --dataset eval/jev/review-routing-validation.json --live --split heldout --variant rename_reverse
```

Default runs send nothing. `--live` uses the configured key for synthetic API
requests, without changing saved Jev settings or dispatching checks. The small
offline test verifies split isolation, gold-label exclusion, unavailable Jev,
mandatory floors, stale/unobserved receipts, recurrence/clarification outcomes,
option meaning preservation and UTF-8 output. These are experiment checks;
they do not certify a new production router or actual API/browser/host behavior.

Initial research verification on 2026-10-07: all three focused offline checks passed; Ruff,
hub lint, repository lint and whitespace checks passed. All eight changed/new
files decode as UTF-8 without BOM. Corpus and document graph were refreshed;
local BM25 retrieved this page for `refactor review criteria evidence Jev`.
The repository-wide debt check remains red on six unchanged existing files:
`tool/agent/chat_session.py`, `tool/main/loop.py`, `tool/main/work.py`,
`tool/test_agent.py`, `tool/test_loop.py` and `web/tests/desktop_browser.py`.
No ratchet entries were raised. A full production suite or actual reviewer
round was not required or run for this isolated research/evaluation change.

Production-increment verification on 2026-10-07: the latest contract suite passed
14 tests; a fresh ten-case loop regression selection passed, including legacy
approval reuse, plan/code composition, stale rounds, final-gate identity, restart
and external implementation ownership. An expanded API/decision/refactoring run
passed 106 tests with two platform skips. These runs overlap; do not sum them as
independent coverage.

The initial 245-case batch reported 240 passes, two skips and three failures
against earlier loaded code/test definitions. They exposed an unnecessary extra
review for legacy approvals, a wrong test constant for read-only tools and a
mapped-check reuse path that skipped preservation. The compatibility guard,
test constant and shared round selection were fixed; all three affected paths
passed fresh runs. The complete batch was not rerun after those fixes.

Ruff, hub/repository lint, whitespace and UTF-8-without-BOM checks passed. The
corpus and document graph were refreshed. The review-profile extraction brings
`loop.py` below its existing line cap; `specs.py` and `verification.py` also stay
within their caps, without raising a ratchet. Repository-wide debt still fails
on five unchanged files: `tool/agent/chat_session.py`, `tool/main/work.py`,
`tool/test_agent.py`, `tool/test_loop.py` and `web/tests/desktop_browser.py`.
No real reviewer was dispatched and no push, merge or deployment was performed.
