# Stage 10 — comparative evaluation and measured rollout

See the [overall design](0-overview.md). Prerequisite:
[stage 9](9-product-observability.md). Fixture collection and baseline runs begin
in stage 1; this stage owns final acceptance and rollout.

Purpose. Establish whether the complete system improves retrieval and decisions
without unacceptable false answers, latency, or cost. Passing mocked tests and
receiving HTTP 200 from Jev are necessary checks, not quality evidence.

## Evaluation data

Start with 12 synthetic smoke intents, including bilingual variants. Before
release, freeze at least 120 distinct intents with both English and Korean
wordings: 240 query variants. Keep paraphrases and translated variants of one
intent in the same split. Use 60 intents for calibration and 60 held-out intents
for evaluation. Do not tune on held-out results and then report them as held-out.
The 60 calibration intents replace stage 6's 35-case synthetic split
(`eval/jev/calibration.json`): refit `eval/jev/policy.json` on them with
`tool/eval/policy.py --collect`, with labels for every decision kind including
the repair and relation Choices, before the held-out run.

Cover these categories in both splits: direct transformation/no retrieval,
explicit factual retrieval, source routing, indirect graph bridges, multi-part
questions, contradictory or superseded decisions, saved-memory retrieval,
paper/research coverage, unanswerable questions, and adversarial/operational
failures. Category counts and exclusions are written before running comparisons.

Labels include required sources and supporting spans, acceptable partial
answers, missing requirements, allowed actions, forbidden actions, and expected
abstention. Some cases legitimately have several correct evidence sets. Keep
those alternatives rather than rewarding a single arbitrary citation order.

Versioned synthetic fixtures contain no real secrets or personal conversations.
Private repository examples live under `raw/eval/jev/` with explicit source
snapshots. Human-written or human-reviewed labels are authoritative; Jev cannot
be its own sole answer-quality judge.

## Controlled comparison

Run four arms on identical source snapshots, questions, answering models, and
maximum context budgets:

| Arm | Retrieval and control |
| --- | --- |
| A | Existing hybrid retrieval and existing answer path |
| B | Hybrid retrieval plus Jev, graph expansion disabled |
| C | Hybrid retrieval plus graph expansion, Jev semantic judgments disabled |
| D | Full graph retrieval, Jev controller, and claim verification |

Use a separate fixed-candidate reranking experiment to isolate Jev's grading
effect from changing candidate recall. Test agent decision points separately
with the same candidate sets and preconditions. Deterministic replay verifies
code behavior; live repeated runs measure model variance. Report both.

Record cold and warm cache conditions separately. For an initial full matrix,
240 query variants across four arms imply 960 answer runs before repetitions;
estimate cost and split into resumable batches rather than starting blindly.
Every batch obeys the stage 1 total experiment ceiling.

## Metrics

| Metric | Definition and purpose |
| --- | --- |
| Supporting-span recall@k | Fraction of required evidence groups recovered, respecting alternative valid spans |
| Bridge recovery | Cases where traversed relationships recover otherwise missing required evidence |
| Source-route false exclusion | Needed source omitted by the controller |
| Evidence false rejection | Supporting passage discarded by grading |
| Unsupported-claim rate | Unsupported factual claims divided by published factual claims |
| Answer coverage | Required question parts answered correctly, preventing abstention from gaming precision |
| Premature sufficiency | Runs declared ready despite missing required evidence |
| Decision correctness | Allowed useful action selected from the presented candidates |
| Authority violations | Invalid/unauthorized actions that reach execution |
| Language gap | Paired English/Korean differences on the same intent |
| Latency and cost | p50/p95, API tokens/cost, generation cost, timeout/fallback rate |

Report denominators, failures, and skipped cases. Compute uncertainty intervals
by resampling intent groups, not treating translations as independent evidence.
Repeat a stratified subset at least three times to expose model variance.

## Release targets

These are proposed acceptance targets, not achieved results. Freeze them before
the held-out run; changing them after failure requires a new evaluation version.

| Gate | Required result |
| --- | --- |
| Deterministic integrity | Zero cross-repository leaks, fabricated accepted locators, unauthorized executions, or duplicated actions |
| Graph benefit | At least 10 percentage points higher supporting-evidence recall on labeled bridge cases than A |
| Overall recall | D loses no more than 2 percentage points versus A overall |
| Answer support | At least 25% relative reduction in unsupported claims, without lower requirement coverage; if A has zero errors, D must also have zero observed errors |
| Decision quality | At least 90% correct allowed choices on the held-out action fixtures; zero execution authority violations |
| Language parity | English/Korean supporting-evidence recall and coverage gaps at most 5 percentage points |
| Added latency | p95 retrieval and verification overhead at most 10 seconds under the defined fixture workload |
| Operating ceiling | No run exceeds configured deadline, call, or token allowance; unknown price remains explicitly unknown |

Small samples cannot prove tiny failure probabilities. If a confidence interval
does not support an improvement claim, report inconclusive and gather additional
predeclared cases. If a gate fails, keep active rollout blocked, diagnose the
responsible stage, and rerun with a new manifest. Do not silently relax thresholds.

## Validation levels

1. Deterministic tests: schemas, provenance, filtering, budgets, permissions,
   cancellation, deletion, and replay. Network disabled by default.
2. Live Jev checks: synthetic connection, then labeled decisions and citations.
3. End-to-end quality: the comparison matrix with independently labeled evidence.
4. Product checks: app/CLI parity, window interaction, reload, project switch,
   translation presentation, and outage recovery.
5. Repository gates: Python tests, Ruff, wiki lint, frontend lint/build, and the
   direct-execution checks required by `docs/development.md` for touched paths.

Freeze source files during long test runs. The search daemon hashes its package;
editing files while tests are running can invalidate test daemons. If that
happens, preserve the original run result and rerun the affected scope after
the code is frozen. Do not combine results into a fictitious single green run.

## Rollout and rollback

Use off → shadow → active. Shadow compares decisions while baseline behavior
continues; it must not execute a proposed action. Enable active mode first for
the selected evaluation repository, then for explicitly configured projects.
Keep source-level and graph-expansion controls independently reversible.

Retain the previous policy, schema reader, and index generation during rollout.
Off mode cancels future controller work and restores baseline retrieval without
destroying documents or historical traces. Pending operations keep their original
mode snapshot or terminate explicitly; they cannot silently switch authority.

Run a synthetic outage and rollback rehearsal before release. Confirm original
checkout state, pending approvals, worktree ownership, memory, and history survive.

## Required completion artifacts

- Dataset and source manifests with hashes and versions.
- Exact commands for baseline, four-arm comparison, decision evaluation, and replay.
- Per-category results, confidence intervals, costs, and representative failures.
- Policy/model/prompt/normalization versions and chosen thresholds.
- Window verification record and rollback rehearsal result.
- Honest outstanding limitations, including host-internal choices outside stage 8 coverage.

Add measured results to this document and link the reproducible report from the
research record. Update each stage's status only after its own gate passes.
Move the series to `docs/plans/done/jev/` only when every mandatory gate is met,
then repair relative links and verify plan discovery. If capability is implemented
but quality remains inconclusive, leave this stage open.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Dataset | Frozen intent groups, labels, source snapshots, splits | Not started |
| 2 | Comparisons | Four arms, fixed-candidate grading, action decisions | Not started |
| 3 | Measurement | Quality, uncertainty, latency, tokens, and cost | Not started |
| 4 | Product | App/CLI parity, window checks, operational failures | Not started |
| 5 | Rollout | Shadow, active canary, rollback rehearsal | Not started |
| 6 | Completion | Reproduction report, all gates, plan archival | Not started |
