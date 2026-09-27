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
the repair and relation Choices, before the held-out run. The relation Choice
has its own split and artifact (`eval/jev/relation.json`,
`eval/jev/relation-policy.json`, `--relation`): stage 7 fitted it on 39
synthetic claim–evidence pairs, and beside it the answers Choice on 42 and the
faithful Choice (recommendations and direct-run text) on 30 of its own
(`eval/jev/answers.json`, `eval/jev/faithful.json`); the calibration intents'
drafted claims replace them, and false acceptance is reported on held-out claims
apart from false rejection. Stage 7's fits only tighten the provisional 0.6/0.2;
loosening one is this stage's call, on held-out false acceptance.

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

## Reproduction

Frozen inputs, committed:

| Artifact | What it holds |
| --- | --- |
| `eval/jev/intents.json` | `jev-intents/1` v2, frozen 2026-09-28 after the label review (v1 2026-09-27): 120 intents × English and Korean (240 variants), 10 categories × 6 calibration + 6 held-out (split by the SHA-256 of the intent id), exclusions none, the synthetic corpus inline (95 pages and 11 papers). Labels: evidence groups with alternatives, `bridged` group, answer parts with references, forbidden assertions, abstention, direct |
| `eval/jev/actions.json` | `jev-action-fixtures/1` v2 (f09 corrected in the label review): 30 held-out fixtures, 10 each for `work.start`, `specs.check`, `loop.fix` |
| `eval/jev/gates.json` | `jev-gates/1` v1: the release targets above as machine-read rules, frozen before any held-out run |
| `eval/jev/calibration.json` | Stage 6's split, now v2: 89 cases derived from the calibration intents alone (`tool/eval/dataset.py --calibration`), replacing the 35 synthetic ones |
| `eval/jev/policy.json` | Refit on it (below); the stage 6 fit stays in git history |

Commands, in order. `tool/eval/compare.py` refuses different options for an
existing run directory, resumes where a batch stopped, and stops every batch at
60 minutes, USD 10 of host spend, or `--jev-tokens` (Jev's price is unknown).

```powershell
python tool/eval/dataset.py --check
python tool/eval/dataset.py --calibration
python tool/eval/policy.py --collect
python tool/eval/compare.py raw/eval/jev/compare-heldout --estimate
python tool/eval/compare.py raw/eval/jev/compare-heldout                      # four arms, retrieval level
python tool/eval/compare.py raw/eval/jev/compare-heldout-answers --level answer  # repeat until no row is left
python tool/eval/compare.py raw/eval/jev/compare-heldout-repeat --ids <stratified subset> --arms B D --repeat 3
python tool/eval/compare.py raw/eval/jev/fixed-heldout --experiment fixed
python tool/eval/compare.py raw/eval/jev/actions-heldout --experiment actions
python tool/eval/report.py raw/eval/jev/compare-heldout raw/eval/jev/fixed-heldout raw/eval/jev/actions-heldout --out raw/eval/jev/report-heldout.json
python tool/eval/rollout.py rehearse
python tool/jev_search.py --replay <tape>                                     # deterministic replay, stage 6
```

`tool/eval/rollout.py canary <checkout>` limits active mode to named checkouts
(`active_projects` in `raw/jev/settings.json`; every other checkout, and a
question with no project, runs shadow). `off` turns Jev off, `follow` drops the
saved mode and the canary. None of them touches documents, indexes, traces or
the policy; a run in flight keeps its settings.

## Measured so far

Nothing below is a held-out result. Everything below was measured on version 1
of both fixture files, whose labels were model-drafted and unreviewed. On
2026-09-27 the user chose to refit on the calibration intents now and run the
held-out comparison after reviewing the labels.

Label review, 2026-09-28. At the user's direction, the labels were reviewed by
a model (`claude-opus-5-5`), not by a person; `labels.reviewed_by` says so and
`report.py` prints the reviewer. What was checked, and what changed, is in each
file's `labels.note`. Version 2 of `intents.json` changes held-out labels only:
bridge-03 and bridge-10 are reworded so they no longer share words with their
bridged page; bridge-01's reference now names Mira for Tuesdays only; route-03's
Korean is clearer. Version 2 of `actions.json` corrects f09, whose finding was
false Python. bridge-02 and bridge-12, in the calibration split, also share
words with their bridged pages; they are left as found because `calibration.json`
and `policy.json` were derived from version 1. The free held-out retrieval run
below is on version 1.

Calibration refit, 2026-09-27. `policy.py --collect` over the 89 derived cases:
172 Jev requests, 126,572 input and 19,978 output tokens, 40 s, model
`jev-1.13.0`, prompt `afa145931e76393e`, normalization `original_english`.

| Kind | Stage 6 rule (no / yes) | Refit | Labels (n, positives) |
| --- | --- | --- | --- |
| route | 0.4 / 0.8 | 0.25 / 0.8 | 89, 83 |
| source | 0.35 / 0.8 | 0.4 / 0.85 | 192, 48 |
| useful | 0.2 / 0.65 | 0.2 / 0.8 | 159, 76 |
| coverage | 0.2 / 0.6 | 0.2 / 0.65 | 83, 48 |
| conflict | 0.4 / 0.8 | 0.05 / 0.8 | 159, 8 |
| redirect | 0.25 / 0.8 | 0.3 / 0.8 | 159, 9 |
| repair (confidence / margin) | 0.6 / 0.2 | 0.5 / 0.2 | 29 cases |

Most source positives land in the uncertain band (38 of 48), which the workflow
searches anyway: coverage over precision. Conflict has only 8 positives, none
answered yes; 49 of 151 negatives and 6 positives fall in its uncertain band,
so this rule rests on too few positives to trust.
The relation, answers and faithful Choices keep stage 7's fit: replacing their
splits needs drafted claims from calibration answers, which need host spend
this session did not use.

Calibration-split comparison, 2026-09-27 (`raw/eval/jev/compare-calibration`,
`fixed-calibration`, `report-calibration.json`; four arms, hybrid, cold, retrieval
level, 60 intents × 2 languages). In-sample: the policy above was fit on these
intents, so this is a development check of the harness and the direction, not
evidence for a gate. 554 Jev requests, 1,196,607 Jev tokens, 4.5 minutes, no
host turn.

| Arm | Recall@8 (96) | Candidate recall | Bridge recall (12) | English − Korean | p95 s | Jev tokens |
| --- | --- | --- | --- | --- | --- | --- |
| A | 0.906 [0.844, 0.958] | 0.906 | 0.583 [0.375, 0.792] | +0.104 [0.031, 0.188] | 0.07 | 0 |
| B | 0.948 [0.901, 0.984] | 0.948 | 0.708 [0.542, 0.875] | +0.021 [−0.021, 0.073] | 2.64 | 459,298 |
| C | 0.906 [0.844, 0.958] | 0.938 | 0.583 [0.375, 0.792] | +0.104 [0.031, 0.188] | 0.06 | 0 |
| D | 0.984 [0.958, 1.0] | 0.984 | 1.0 [1.0, 1.0] | +0.031 [0.0, 0.083] | 1.71 | 532,604 |

D − A: recall +0.078 [0.031, 0.135], bridge recall +0.417 [0.208, 0.625]; D − B
bridge recall +0.292 [0.125, 0.458]. In B and D: no Jev fallback, no route
false exclusion, no false rejection, premature sufficiency 1 of 96 in B and 0
in D, direct answers 10 of 12 where labelled direct and none where evidence
was needed. Integrity errors and Budget breaches 0 in every arm. Fixed
candidates (54 requests over arm C's candidates at k = 12): false rejection 0
of 60 supporting passages (4 uncertain), false acceptance 0.0085 of 588
(22 uncertain), recall@4 RRF 0.958 against Jev's order 0.969, all 51
adversarial pages flagged for redirect and none of the other 597. Read against
the frozen gates, as a preview only: graph benefit and overall recall above target, language
parity 0.031 under 0.05, added latency 1.6 s; answer support and decision
quality unmeasured (host spend, held-out fixtures).

Free held-out retrieval, 2026-09-27 (`raw/eval/jev/compare-heldout-free`, arms A
and C, hybrid e5 + BM25, no request sent). This run was started before the
sequencing decision above; it tunes nothing and is recorded as it came. Recall@8
of supporting-evidence groups: A 0.912 [0.854, 0.964], C 0.912 (96 evidence
variants); bridge recall 0.625 for both (12). Candidate recall, counting what
was retrieved but past k: A 0.912, C 0.948. English minus Korean recall: +0.094
[0.010, 0.177] in both arms.

Finding for stage 5. Without Jev, the graph lane's passages reach the dossier
but are cut past k: `Flow.ranked` puts ungraded chunks in retrieval order, RRF
seeds fill k, and the walked passages land in `limits.beyond_k`. The rota page
of `bridge-01` is one. So C hands the answer exactly what A does; the graph can
only change an answer where Jev grades it forward (B→D). Left as measured: the
held-out comparison decides whether D meets the graph-benefit gate, and a change
to ranking would be a new evaluation.

Rollback rehearsal, 2026-09-27 (`python tool/eval/rollout.py rehearse`, also
`tool/test_evaluation.py`): 21 of 21 checks. Canary active in its checkout and
shadow elsewhere and for the hub; an outage (Jev's host pointed at a closed local
port) ended `unavailable` with reason `network` while baseline retrieval still
found the page; switching off mid-run left the run on its active snapshot and
new work off with nothing asked; `follow` returned to the `.env`'s shadow; the
checkout's HEAD, status and files, the worktree and its HEAD, the memory, the
pending approval (recorded, never executed) and the run history came through
unchanged.

Held-out, retrieval level, 2026-09-28 (`raw/eval/jev/compare-heldout`,
`fixed-heldout`, `actions-heldout`, `report-heldout.json`; intents v2 and actions
v2, commit `5a63a7b`, clean; four arms, hybrid, cold, k = 8, 60 intents × 2
languages). This is the held-out half: nothing was fitted or changed on it.
Jev requests only, no host turn: 579 requests, 1,193,912 Jev tokens, 4.3 minutes.

| Arm | Recall@8 (96) | Candidate recall | Bridge recall (12) | English − Korean | p95 s | Jev tokens |
| --- | --- | --- | --- | --- | --- | --- |
| A | 0.896 [0.833, 0.953] | 0.896 | 0.583 [0.417, 0.792] | +0.104 [0.021, 0.208] | 0.03 | 0 |
| B | 0.953 [0.912, 0.990] | 0.953 | 0.625 [0.5, 0.792] | −0.010 [−0.031, 0.0] | 2.47 | 451,713 |
| C | 0.896 [0.833, 0.953] | 0.938 | 0.583 [0.417, 0.792] | +0.104 [0.021, 0.208] | 0.02 | 0 |
| D | 1.0 [1.0, 1.0] | 1.0 | 1.0 [1.0, 1.0] | 0.0 [0.0, 0.0] | 1.60 | 518,656 |

D − A: recall +0.104 [0.047, 0.167], bridge recall +0.417 [0.208, 0.583] (6
intents); D − B bridge recall +0.375 [0.208, 0.5]; C − A 0, as the stage 5
finding below predicts. Fixed candidates (54 requests over arm C's candidates at
k = 12): false rejection 0 of 57 supporting passages (3 uncertain), false
acceptance 0.010 of 591 (19 uncertain), recall@4 RRF 0.938 against Jev's order
0.948; 49 of 50 adversarial pages flagged for redirect and none of the other
598. Actions (30 requests): 29 of 30 chosen right. work.start 10/10 (2
uncertain, both ran a baseline equal to the label), specs.check 10/10,
loop.fix 9/10 (f04, a finding resting on a shared rule, came back uncertain
and ran the baseline `fix` where the label is `context`); no authority violation
and no unoffered candidate executed.

Gates against v1 of `gates.json`: integrity, graph benefit, overall recall,
decision quality (0.967), language parity (0.0), added latency (1.57 s) and
operating ceiling pass. Answer support is not measured: it needs the answer
level, which spends host turns. The labels were reviewed by a model, not a
person, and the report says so.

Limits of what is above. In arms B and D a Korean wording goes through the
product's translator before Jev sees it, as it does in the app; those short
calls are not counted in `host_turns` or USD, and they are the one spend
outside "Jev requests only". The stage 7 relation fit is kept, not refit
(host spend). Window checks for this stage were not run.

Left before completion, in order:

1. Label review — done 2026-09-28 by a model at the user's
   direction (above). A label changed after that is a new version.
2. Held-out retrieval level, fixed and action experiments — done 2026-09-28
   (above). Still to run: answer level (`--level answer`, host spend under the
   ceiling), which the answer-support gate needs, and the 3× repetition subset;
   then `report.py` over all of them.
3. Every gate `pass`, then `rollout.py canary <this checkout>`; `.env` stays
   `WIKI_JEV_MODE=shadow` until the canary has run without a rollback.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Dataset | Frozen intent groups, labels, source snapshots, splits | In progress — `eval/jev/intents.json` and `actions.json` frozen with corpus hashes and splits; labels reviewed 2026-09-28 by a model at the user's direction, version 2 of both |
| 2 | Comparisons | Four arms, fixed-candidate grading, action decisions | In progress — `tool/eval/compare.py` runs all three; held-out retrieval level, fixed and actions run 2026-09-28; answer level and the repetition subset not run |
| 3 | Measurement | Quality, uncertainty, latency, tokens, and cost | In progress — `tool/eval/report.py`: intent-resampled intervals and the frozen gates; seven of eight gates pass on the held-out half, answer support not measured |
| 4 | Product | App/CLI parity, window checks, operational failures | In progress — the canary in settings, the API and the window's mode line; window checks not run this stage |
| 5 | Rollout | Shadow, active canary, rollback rehearsal | In progress — canary, off and follow commands; rehearsal passed; active canary waits for the gates |
| 6 | Completion | Reproduction report, all gates, plan archival | Not started |
