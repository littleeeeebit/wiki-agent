# PR 5 — establish current English reliability

Freeze a trustworthy English baseline before changing Korean input behavior.

## Work

Keep the owner-approved quality tradeoff; do not tighten or relax it silently.
Keep old evaluation cases as regression data. Create a new calibration split and
fresh held-out intent groups, with reviewed labels, immutable source snapshots,
code/prompt/policy/model versions and preregistered gates.

Include factual lookup, analysis versus fact, pasted material versus requirements,
missing evidence, conflicts, bridge evidence, decisions, work start, review fixes,
uncertain routing, cancellation and provider failure. Evaluate English input over
English evidence separately from translated evidence. Cover the newer unverified
analysis path and ensure it cannot be presented or later remembered as verified.

Reuse the existing comparison arms and runners. Measure routing mistakes, unnecessary
calls, graph contribution, coverage, unsupported claims, latency and total cost.
Report abstention and unverified analysis separately; changing an answer's label
must not manufacture a quality gain. Add repetition around uncertain decisions.

Do not tune on new held-out failures and still call the same set unseen. A policy
change creates a new version and fresh generalization evidence where needed.

## Evidence and exit

Frozen gates pass or are explicitly inconclusive/failing. Retain confidence
intervals, raw run references and reproduction commands. Include current behavior
in the version manifest, not only the old PROMPTS hash. Operational graph health
and retrieval usefulness have separate results.

## Scope and rollback

No Korean fixes in this PR. Existing active mode is an owner decision; any rollout
change is explicit and reversible through current settings. Evaluation costs must
fit a configured finite budget.

## Implementation blueprint

### Dataset and artifact contract

Add a new immutable dataset under `eval/jev/reliability/`; retain existing
`eval/jev/intents.json` as regression evidence. Extend `eval.dataset` and
`eval.compare` to accept an explicit `--dataset` path (proposed option).
No overwriting the old labels to make existing commands appear improved.

Use 12 intent families, initially 12 independent intents each: six calibration and
six held-out. Families: direct/factual, routing, missing evidence, conflicts, bridge,
memory/decisions, adversarial instructions, analysis versus fact, pasted material,
work-start choices, review-fix choices, and failure/cancel behavior. A translated
variant is not an independent sample. Expand sample size only before freezing or
as a separately versioned experiment; intervals may remain inconclusive.

Each item stores ID, split, English query, source snapshot hashes, required evidence,
acceptable transition set, forbidden operations, expected verification category,
and label-review provenance. Two labels feed the new routing measurements, kept
apart from the answer-grading `parts` (whose `ask` stays the question text the
grader reads, and whose entries alone form the coverage denominator):

- `analysis` on the intent: `true` when the query asks for the assistant's own
  comparison, judgment or advice.
- `route_segments` on the intent: `[{"text": ..., "ask": true|false}]`, one entry
  per segment `knowledge.requirements` produces from the stored English query, in
  order and verbatim, labelled request (`true`) or pasted material (`false`).
  They are generated from the segmenter at freeze time and then reviewed; the
  segmenter is part of `behavior_manifest`. Supplied material appears only here,
  never in `parts`.
- `route_expected` on the intent: `false` where the reviewed correct behavior
  ends before route verdicts are applied (the failure/cancel family's cancelled,
  exhausted or unavailable paths); `true` otherwise.
- `fault` on each failure/cancel intent, so the family is reproduced rather than
  waited for: `{"kind": "cancelled"|"exhausted"|"unavailable", "phase": "route",
  "expect": {"status": ..., "reason": ...}}`. A label cannot cause an outage;
  the runner applies it (below).

Preserve the existing runner's required variant
shape where compatibility needs it, but execute only English in this stage.
Review ambiguous labels before reveal; record whether review was human or model
and preserve the existing provisional-label rule.

Run manifest:
`{dataset_hash, actions_hash, gates_hash, source_hashes, code_commit, behavior_manifest,
models, policies, cache_mode, seed, environment, limits, label_review}`.
Refuse comparison aggregation when these are incompatible.

### Gates and comparisons

Copy existing gates v3 without changing their accepted thresholds:
integrity violations zero; bridge recall improvement 0.10; overall recall lower
bound at least -0.02; unsupported claims improve by 25% with coverage lower bound
at least -0.20; action accuracy at least 0.90; added p95 latency at most 10 s;
zero operating-ceiling breaches. Keep their existing statistical definitions.

Add separate current-path measurements. Analysis text remains unverified and is
never included as verified claims to inflate support. Report correct analysis/fact
routing and requirement/material classification with intervals. Incorrect verified
labeling and unauthorized actions are integrity failures.

Recording and scoring changes (proposed):

| Location | Change |
| --- | --- |
| `knowledge.Flow.route` | Record a proposed dossier field `route_segments`, `[{text, ask}]` in segment order, when the route verdicts are applied: kept segments `ask: true`, `material` `ask: false`. Recorded there because `Flow.split` later replaces a single segment's `requirements` with model-split asks |
| `compare.dossier_row` | Export the dossier's `analysis`, `question_en` and `route_segments` |
| `compare` intent export | Carry `analysis` and `route_segments` beside `parts`; `compare.graded` and its `parts` export (`compare.py:297`) stay unchanged |
| `report.py` | Score `analysis_routing` (row flag against the intent label) per case. Score `segment_classification` by matching row segments to `route_segments` on exact text: within the cohort below, a case with no `route_segments` (route not reached), a `question_en` different from the stored query, or different segment texts is counted as `unscorable`, never as right. Interval from the existing `report.boot` over intent groups |
| `eval.policy.labelled` | Unchanged. ASK/ANALYSIS thresholds are not fitted in this PR; fitting them is a separately versioned policy change |

Each of the two new measurements, and each new action family below, gets its own
gate with target 0.90. It passes when the interval's lower bound is at least 0.90,
fails when the upper bound is below 0.90, and is inconclusive otherwise, as is a
result with no interval.

`analysis_routing` and `segment_classification` apply only to a preregistered
cohort, frozen in the gates file: arms B and D (A and C run with Jev off,
`compare.ARMS`, and return to baseline before routing) crossed with held-out
intents whose `route_expected` is `true`. Rows outside the cohort are not
applicable to these two gates; the failure/cancel family's rows are judged by
their expected terminal status and reason code, reported as a separate
operational result. Within the cohort, a row with no `route_segments` is
`unscorable`, and any unscorable row makes both gates inconclusive; the count
is reported beside them. The existing
gate v3 `decision_quality` keeps its point estimate unchanged; the new gates are
added beside it, never substituted.

Run existing A/B/C/D arms for retrieval as defined in `compare.py`; do not redefine
their meanings in the new report. Run answer-level A/D comparison and action
experiments. Run three repetitions of a stratified subset for boundary choices.
Graph ablation compares source/evidence recall, not node count.

Action experiments do not run from intents. `compare.py` loads its fixtures from the
fixed `ACTIONS` path (`eval/jev/actions.json`) and `offered_and_state` reads
point-specific fields, so a `--dataset` option alone would still score the old,
already-seen action labels. Add a fresh `eval/jev/reliability/actions.json` in the
existing `jev-action-fixtures/1` schema, written without reference to the old
fixtures, with the fields `offered_and_state` reads per point: `spec` for
`work.start`; `goal`, `criteria`, `changed_files` and `checks` for `specs.check`;
`round`, `findings` and `disputed` for `loop.fix`. Six calibration and six
held-out fixtures per point, each with `split`, `label` and label-review provenance.
Replace the `ACTIONS` constant with a proposed `--actions` path (default unchanged);
its hash already goes into the run's `fixtures` option and must also appear in the
run manifest, so `report` refuses action results whose fixture hash differs from
the manifest's.

### Execution recipe

Before paid execution, use the runner's `--estimate` and require configured
time/call/token ceilings; cost is additionally capped when pricing is available.
The following commands use the proposed `--dataset` and `--actions` additions:

```text
python tool/eval/compare.py raw/eval/jev/reliability-calibration --dataset eval/jev/reliability/intents.json --split calibration --languages en --estimate
python tool/eval/compare.py raw/eval/jev/reliability-heldout --dataset eval/jev/reliability/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-answers --dataset eval/jev/reliability/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-actions --experiment actions --dataset eval/jev/reliability/intents.json --actions eval/jev/reliability/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-heldout raw/eval/jev/reliability-answers raw/eval/jev/reliability-actions --out raw/eval/jev/reliability-report.json
```

Every run, the action run included, passes the reliability `--dataset`:
`compare.main` loads an intent dataset for every experiment and `compare.opened`
records its identity in `run.json` (today hard-coded to `eval/jev/intents.json`,
`compare.py:86–89`). `opened` must record the path and hash actually loaded, and
`report` refuses runs whose dataset identities differ.

Fault injection (proposed, in `compare.run_arms.one`): for an intent with a
`fault`, run arms B and D only (A and C never reach Jev), with the decision cache
off so the route request is not served from cache. `Flow.evaluate` is an
instance attribute that `knowledge.prepare` sets to
`functools.partial(decision.evaluate, cfg)` (`knowledge.py:950–952`), so the
runner cannot reach it; instead it patches `decision.evaluate` around the
`prepare` call with a wrapper that raises for the fixture's phase — the `stage`
argument, which `decision.contract.decide` fills from the request's
`decision_kind` (`contract.py:129`), `route` here — and delegates every other
stage to the original. It raises what `test_decision_flow` already maps to each status —
`decision.JevError("cancelled")` → `cancelled/cancelled`, `Exhausted("calls")` →
`exhausted/calls`, `decision.JevError("timeout")` → `unavailable/timeout`. The row
records `fault` (kind, phase, raised exception) as provenance. The failure/cancel
operational result passes a row only when its dossier `status` and `reason` equal
`fault.expect`; a natural provider failure on any other row is an `answer_error`
or run failure, never evidence for this family.

`report.build` today keys runs by experiment alone (`found["runs"][experiment]`,
`found["arms"]`), so the answer run, also `experiment: arms`, overwrites the
retrieval run. Key both by experiment and level (`arms/retrieval`,
`arms/answer`, `actions`) and refuse two folders for the same key. Score
`analysis_routing` and `segment_classification` from the `arms/retrieval` run
only, which must contain both B and D over the whole cohort; score answer gates
from `arms/answer`, and action gates from `actions`. A gate whose required run is
missing, or whose run lacks any cohort row, is `not_measured`, which does not
pass and leaves the stage open.

A row present but unmeasured is the same gap. `run_arms` records `answer_error`
when answering or grading fails, `report.values` then omits that row's quality
metrics, and `arms_report.paired` compares only surviving pairs — so five failed
D answers out of twelve can still pass `answer_support` on seven pairs. The
answer cohort is preregistered like the routing one: arms A and D × held-out
intents without a `fault`. Any cohort row with `answer_error`, no `grade`, or a
`grade` carrying `error` makes every answer-level gate `inconclusive`, with the
count reported beside it. v3's thresholds and statistics are applied unchanged
to a complete cohort only.

The implementation must add dataset/gate manifest propagation to report rather
than letting it load unrelated default labels; this includes the label-review
check, which reads the action fixtures from the fixed `compare.ACTIONS` today and
must read the run's own `--actions` file. A cancelled run is resumable by
immutable case ID and manifest; changed manifests require a new directory.

### Decision and failure protocol

Calibrate only on calibration cases. Freeze the signed/hash-addressed manifest
before held-out execution. A failure produces a diagnosis and next version; it
does not edit the frozen gate. Old held-out cases may become regression fixtures,
but the next generalization claim needs unseen cases.

Exit requires passing mandatory gates, reviewed labels under the chosen policy,
and current analysis-path evidence. Run budget exhaustion, insufficient labels or
statistically inconclusive results leave the stage open. Store English baseline ID
for PR 9. Rollback restores prior policy references without deleting failed runs.

## Results

Four frozen sets, all English input over English evidence, all under gates v3
(`gates.json` byte-identical across them), and a fifth under gates v5, which
splits routing per class (see "v5 — enough cases to decide"). The runs live
under `raw/eval/jev/`, which git ignores; the reproduction commands below
rebuild them.

### v1 — `eval/jev/reliability/` at 8588d03

144 intents and 36 action fixtures over the synthetic Kestrel corpus, labels
reviewed by gpt-6-sol (model review, five rounds). Held-out runs:
`reliability-heldout-clean` (retrieval), `reliability-answers`,
`reliability-repeat`, `reliability-actions`; report
`reliability-report.json`.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 2 | fail (scorer defect, below) |
| Graph benefit | 0.10 | 0.333 [0.167, 0.5] | pass |
| Overall recall | ≥ -0.02 | 0.036 [0.009, 0.071] | pass |
| Answer support | 0.25, coverage ≥ -0.20 | 0.749, coverage D−A -0.174 [-0.258, -0.099] | fail |
| Decision quality | 0.90 | 1.0 | pass |
| Added latency | ≤ 10 s | -0.73 s | pass |
| Operating ceiling | 0 | 0 | pass |
| Analysis/fact routing | 0.90 | 0.955 [0.894, 1.0] | inconclusive |
| Request/material classification | 0.90 | 1.0 | pass |
| Work start / check / review fix | 0.90 | 1.0 / 1.0 / 1.0 (n=6 each) | pass |

Answer support failed on coverage: D withheld answers where Jev scored a claim,
a route or a coverage question uncertain (D coverage 0.826 against A's 1.0).
That diagnosis came from held-out rows, so its fix could not be judged on them.

### v2 — `eval/jev/reliability-v2/` at 75dd67f

The fix: when Jev leaves a question uncertain, `contract.decide` asks the run's
own host model once per request; an answer resting on the host's word is
published and remembered `host_checked`, never verified (252d0f9, code review by
gpt-6-sol in four rounds). The v2 set keeps v1's 72 calibration intents and 18
calibration fixtures verbatim and holds out 72 intents and 18 fixtures written
after v1's reveal, in v1's held-out strata (labels reviewed by gpt-6-sol in three
rounds; approved hashes: intents `0321f73a`, actions `c0b0f55d`). Runs:
`reliability-v2-calibration`, `reliability-v2-heldout`, `reliability-v2-answers`,
`reliability-v2-repeat`, `reliability-v2-actions`; report
`reliability-v2-report.json`.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 0 | pass |
| Graph benefit | 0.10 | 0.417 [0.25, 0.5] | pass |
| Overall recall | ≥ -0.02 | 0.045 [0.009, 0.080] | pass |
| Answer support | 0.25, coverage ≥ -0.20 | 0.768, coverage D−A -0.106 [-0.189, -0.038] | pass |
| Decision quality | 0.90 | 1.0 | pass |
| Added latency | ≤ 10 s | 19.3 s | **fail** |
| Operating ceiling | 0 | 0 | pass |
| Analysis/fact routing | 0.90 | 0.947 [0.894, 0.992] | inconclusive |
| Request/material classification | 0.90 | 1.0 | pass |
| Work start / check / review fix | 0.90 | 1.0 / 1.0 / 1.0 (n=6 each) | pass |

Retrieval, held out, each arm over its own rows (recall@8 with interval; bridge
recall n=6; p95 s). B and D also run the six injected-failure intents, so their
recall has n=62 against A's and C's 56. The overall-recall gate pairs D and A
over the 56 intents both ran, so it is not the difference of these columns:

| Arm | n | Recall | Candidate recall | Bridge recall | p95 s | Host USD |
| --- | --- | --- | --- | --- | --- | --- |
| A | 56 | 0.902 [0.830, 0.964] | 0.902 | 0.583 | 0.02 | 0 |
| B | 62 | 0.839 [0.750, 0.919] | 0.839 | 0.5 | 17.4 | 2.33 |
| C | 56 | 0.902 [0.830, 0.964] | 0.946 | 0.583 | 0.03 | 0 |
| D | 62 | 0.887 [0.807, 0.968] | 0.887 | 1.0 | 17.4 | 2.36 |

Answers, held out: coverage A 0.977, D 0.871 [0.788, 0.939]; unsupported claim
rate A 0.397, D 0.092 [0.008, 0.193]. D's 66 answers: 31 verified, 18
host-checked, 13 abstained, 4 unverified analysis; none of the analysis or
host-checked ones was published or remembered as verified. Failure and
cancellation: 12 of 12 ended with the expected status and reason. Repetition
(8 boundary intents × 3, B and D): status the same in 8 of 8 groups for both
arms, evidence the same in 5 (B) and 3 (D) of 8.

Host fallback, questions sent to the host / questions asked, arm D, answer run:

| Kind | Fell | Kind | Fell |
| --- | --- | --- | --- |
| repair | 66/66 (settled 5) | answers | 28/172 |
| source | 154/288 | relation | 18/121 |
| analysis | 18/72 | coverage | 7/86 |
| conflict | 192/780 | useful | 36/780 |
| faithful | 3/10 | route | 2/72 |
| ask | 1/19 | redirect | 4/780 |

Actions: work start fell to the host 2 of 6 times, check and review fix never.

Time and host spending per batch. The runner stops a batch at 60 min or USD 10
of host spending, checked after each row: calibration 25.8 min / USD 4.70,
held-out retrieval 26.4 / 4.69, answers 49.1 / 10.03 (stopped after the row
that crossed USD 10, 122 of 138 rows) then 4.1 / 0.77, repetition 12.4 / 2.23,
actions 0.2 / 0.02. Host spending totals USD 22.44. The dollars and the
threshold count host turns only. Jev's 915 requests and 1,871,323 tokens have
no dated price, so the total provider cost is unknown.

### v3 — `eval/jev/reliability-v3/` at 1e80cf5

The fix for v2's two open gates, ef45fec:

- The host hears only what code cannot settle. `decision.CODE_SETTLES`
  (source, useful, conflict, redirect, repair) are kinds whose uncertain
  verdict code already takes a safe way:
  - an uncertain source is searched;
  - an uncertain useful or conflict passage stays evidence;
  - an uncertain redirect is flagged untrusted;
  - an uncertain repair takes code's order.

  On v2 held-out these kinds cost a typical D row two host turns of about
  5 s each. The host settled 5 of 66 repairs.
- The analysis question says that a calculation, a conversion, a count, a
  sort or a rewording of given text is not analysis. Every v2 analysis/fact
  miss was such a task, direct-05 in calibration included.

The v3 set keeps the same calibration half and adds 72 held-out intents
written after v2's reveal (review r3, gpt-6-sol, two rounds; approved input
intents `cc5c4643`). The action fixtures are v2's, unchanged: the action path
did not change, so the action gates here are regression evidence, not a fresh
action sample. On calibration first (v2's split, runs
`reliability-v3-calibration` and `reliability-v3-calibration-answers`, at
ef45fec):
- recall was the same as v2 in every arm;
- D retrieval p95 fell from 16.0 s to 5.4 s;
- analysis/fact misses fell from 2 to 0;
- added latency was 8.1 s, and coverage D−A −0.030.

Held-out runs: `reliability-v3-heldout`, `reliability-v3-answers`,
`reliability-v3-repeat`, `reliability-v3-actions`. The report is
`reliability-v3-report.json`.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 0 | pass |
| Graph benefit | 0.10 | 0.5 [0.5, 0.5] | pass |
| Overall recall | ≥ -0.02 | 0.054 [0.018, 0.098] | pass |
| Answer support | 0.25, coverage ≥ -0.20 | 0.720, coverage D−A -0.053 [-0.114, 0.0] | pass |
| Decision quality | 0.90 | 1.0 | pass |
| Added latency | ≤ 10 s | 10.76 s | **fail** |
| Operating ceiling | 0 | 0 | pass |
| Analysis/fact routing | 0.90 | 0.970 [0.924, 1.0] | pass |
| Request/material classification | 0.90 | 1.0 | pass |
| Work start / check / review fix | 0.90 | 1.0 / 1.0 / 1.0 (n=6 each) | pass |

Retrieval, held out. The n column is as in v2: B and D include the six
injected-failure intents.

| Arm | n | Recall | Candidate recall | Bridge recall | p95 s | Host USD |
| --- | --- | --- | --- | --- | --- | --- |
| A | 56 | 0.946 [0.902, 0.982] | 0.946 | 0.5 | 0.02 | 0 |
| B | 62 | 0.879 [0.798, 0.944] | 0.879 | 0.417 | 6.2 | 0.18 |
| C | 56 | 0.946 [0.902, 0.982] | 1.0 | 0.5 | 0.02 | 0 |
| D | 62 | 0.935 [0.871, 0.984] | 0.935 | 1.0 | 6.3 | 0.19 |

Answers, held out:
- Coverage: A 0.992, D 0.939 [0.886, 0.985].
- Unsupported claim rate: A 0.397, D 0.111 [0.045, 0.175].
- D's 66 answers: 31 verified, 18 host-checked, 9 abstained, 5 unverified
  analysis, 3 direct. None of the analysis or host-checked ones was published
  or remembered as verified.
- Failure and cancellation: 12 of 12 as expected.
- Repetition: status the same in 8 of 8 groups, evidence the same in 4 (B)
  and 3 (D).

Host fallback on D's answer run:
- No question of a `CODE_SETTLES` kind went to the host.
- Analysis 9/72, coverage 6/85, route 2/72.
- Claim checks: answers 40/218, relation 35/157, faithful 2/14.
- Actions: work start 2/6, review fix 1/6.

Host spending per batch: retrieval 4.0 min / USD 0.37, answers 45.9 / 9.34
(one batch), repetition 2.9 / 0.56, actions 0.3 / 0.04. Calibration was 3.8 /
0.38 and 48.2 / 9.77. Jev's cost has no dated price, as in v2.

### v4 — `eval/jev/reliability-v4/` at a0e88d3

The fix for v3's latency, 02fab78, was diagnosed on the calibration half only.
Most second drafts came from one prompt sentence:
- The draft prompt said every claim names the whole question as `r0`.
- `requirements()` numbers a question's parts from `r0`. When routing keeps a
  pasted first part as material, `r0` leaves the requirements, and the kept
  part stays `r1` or later.
- The drafter still named `r0`, so every claim of the first draft was
  rejected as `unknown_requirement`, and the answer drafted again.

The rejection reasons behind this came from a targeted diagnostic: the eight
calibration rows that drafted twice under v3 were rerun with
`knowledge.grounded` wrapped to log each draft's rejected claims. Its records
were scratch and are not retained; the runs below record only how many drafts
each answer took. The prompt now says to name only the ids `requirements`
lists, which need not start at `r0`.

What the retained calibration runs show, D arm (`reliability-v3-calibration-answers`
at ef45fec against `reliability-v4-calibration-answers` at 02fab78, the same
calibration half):
- rows that drafted twice fell from 8 to 3. v3: pasted-01, 04, 06, 07, 08
  and 09, adv-09, bridge-07. v4: pasted-06, direct-01, fix-03;
- added latency fell from 8.10 s to 6.25 s (D p95 29.15 s to 26.86 s);
- answer support rose from 0.687 to 0.736.

Nothing else was changed for v4.

The v4 set keeps the same calibration half and adds 72 held-out intents
written after v3's reveal (review r4, gpt-6.1-sol; approved input intents
`b96ad318`). The review was one round over all 72 intents, which found five
to fix, then a round over those five revisions, which found nothing. The
frozen r4 note says "two full-set rounds", which overstates the second round;
it stays as frozen because the runs record the dataset's hash. Two earlier
rounds were answered by gpt-6-luna and are not counted: the reviewer cell had
been launched with a pinned model. The action fixtures are v2's, unchanged.

Held-out runs: `reliability-v4-heldout`, `reliability-v4-answers`,
`reliability-v4-repeat`, `reliability-v4-actions`. The report is
`reliability-v4-report.json`.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 0 | pass |
| Graph benefit | 0.10 | 0.25 [0.083, 0.417] | pass |
| Overall recall | ≥ -0.02 | 0.027 [0.0, 0.063] | pass |
| Answer support | 0.25, coverage ≥ -0.20 | 0.822, coverage D−A -0.038 [-0.076, -0.008] | pass |
| Decision quality | 0.90 | 1.0 | pass |
| Added latency | ≤ 10 s | 5.48 s | pass |
| Operating ceiling | 0 | 0 | pass |
| Analysis/fact routing | 0.90 | 0.970 [0.924, 1.0] | pass |
| Request/material classification | 0.90 | 1.0 | pass |
| Work start / check / review fix | 0.90 | 1.0 / 1.0 / 1.0 (n=6 each) | pass |

Re-judged 2026-10-10 (external audit, Q1). The table above is as recorded. Its
rate intervals came from a percentile bootstrap, which collapses to [1, 1] when
every case succeeds, so six of six passed a 0.90 lower-bound gate. A rate now
takes a 95% Wilson interval: all-success needs about 35 cases to clear 0.90.
The same v4 runs, re-reported (`reliability-v4-report-wilson.json`):

| Gate | Wilson interval | Result |
| --- | --- | --- |
| Analysis/fact routing | 0.970 [0.896, 0.992], n=66 | inconclusive |
| Request/material classification | 1.0 [0.945, 1.0] | pass |
| Work start / check / review fix | 1.0 [0.610, 1.0] (n=6 each) | inconclusive |

Every other gate is unchanged. Under the exit rule above, inconclusive results
leave the stage open: the action gates need roughly 35 held-out fixtures each,
and routing a few more cases or a larger margin. The same audit (K2) changed
the duplicate key to the original text, with only line endings, trailing
spaces and blank edge lines set aside; arms A and C on v4's held-out half gave identical rows before
and after (`k2-check-before`, `k2-check-after`).

Retrieval, held out. The n column is as in v2: B and D include the six
injected-failure intents.

| Arm | n | Recall | Candidate recall | Bridge recall | p95 s | Host USD |
| --- | --- | --- | --- | --- | --- | --- |
| A | 56 | 0.973 [0.946, 1.0] | 0.973 | 0.75 | 0.02 | 0 |
| B | 62 | 0.911 [0.839, 0.968] | 0.911 | 0.75 | 5.3 | 0.22 |
| C | 56 | 0.973 [0.946, 1.0] | 1.0 | 0.75 | 0.02 | 0 |
| D | 62 | 0.935 [0.871, 0.984] | 0.935 | 1.0 | 5.1 | 0.22 |

Answers, held out:
- Added latency: row p95 D 25.01 s against A 19.53 s.
- Second drafts in D: 2 rows (start-30, fix-28), against 9 in v3's held-out
  half.
- Coverage: A 1.0, D 0.962 [0.924, 0.992].
- Unsupported claim rate: A 0.403, D 0.072 [0.033, 0.114].
- D's 66 answers: 29 verified, 21 host-checked, 7 abstained, 5 unverified
  analysis, 4 direct. None of the analysis or host-checked ones was published
  or remembered as verified.
- Failure and cancellation: 12 of 12 as expected.
- Repetition: status the same in 8 of 8 groups, evidence the same in 5 (B)
  and 3 (D).

Host fallback on D's answer run:
- No question of a `CODE_SETTLES` kind went to the host.
- Analysis 9/72, coverage 6/75, route 1/72.
- Claim checks: answers 32/188, relation 34/149, faithful 3/14.
- Actions: work start 2/6.

Host spending per batch: retrieval 3.7 min / USD 0.44, answers 42.9 / 8.85
(one batch), repetition 1.6 / 0.22, actions 0.2 / 0.02. The calibration
answers were 45.0 / 9.30. Jev's cost has no dated price, as in v2.

### v5 — `eval/jev/reliability-v5/` at 21dff77

Frozen after label review r5 (see "Frozen"). The calibration is
recorded under "Calibration at bc838d8"; after the grader fix (#109) its
answers ran again, complete, as `reliability-v5-calibration-answers-2` at
21dff77: unsupported claim rate A 0.192 (500 claims), D 0.218 (496), answer
support -0.134, coverage D−A 0.0. Graded against what the host read, the
ordinary answer path gains nothing from Jev on support. A read files in 63 of
66 rows and D in 20 of 72.

The held-out answers were therefore not run, at the owner's direction: answer
support would fail on calibration's evidence, and the open question is the
default answer path (#59), not this set. Answer support and added latency are
`not_measured` in the table below. After the answer path changed, the
held-out answers ran at ac90615 and failed both: answer support 0.093 and
added latency 17.49 s ([answer-path.md](answer-path.md), "Held-out answers
at ac90615"). Its rerun after L1, at 01702da, is on a revealed cohort and
does not change that result.

Held-out runs: `reliability-v5-heldout`, `reliability-v5-repeat`,
`reliability-v5-actions`. The report is `reliability-v5-report.json`. Rates
take the 95% Wilson interval; routing counts each intent once over B and D.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 0 | pass |
| Graph benefit | 0.10 | 0.417 [0.25, 0.5] | pass |
| Overall recall | ≥ -0.02 | 0.031 [-0.004, 0.066] | pass |
| Answer support | 0.25 | — | not_measured |
| Decision quality | 0.90 | 0.963 | pass |
| Added latency | ≤ 10 s | — | not_measured |
| Operating ceiling | 0 | 0 | pass |
| Analysis recall | 0.90 | 1.0 [0.934, 1.0], n=54 | pass |
| Fact specificity | 0.90 | 0.889 [0.796, 0.943], n=72 | inconclusive |
| Material classification | 0.90 | 0.973 [0.892, 0.994], n=56 | inconclusive |
| Request-only multipart classification | 0.90 | 1.0 [0.936, 1.0], n=56 | pass |
| Work-start choice | 0.90 | 0.944 [0.849, 0.981], n=54 | inconclusive |
| Check choice | 0.90 | 1.0 [0.935, 1.0], n=55 | pass |
| Review-fix choice | 0.90 | 0.944 [0.849, 0.981], n=54 | inconclusive |

The misses, each in both arms unless named:
- Fact specificity, 8 of 72 routed as analysis: four of the six facts
  phrased as advice (analysis-85, 86, 89 and 90, among them "What should I do
  if I accidentally commit an API key?"; 87 and 88 were routed right), and
  pasted-40, start-33, start-35 and fix-31, each a lookup asked with "how",
  "when" or "which" and "should". This is v3's and v4's family again; 72 cases
  allow two misses, and eight is six past that.
- Material classification, 2 of 56: analysis-82 ("Board question: …") kept
  the quoted question as a request; none-35 did in B only.
- Work start, 3 of 54: v5ws25, 27 and 29, dispatch chosen as evidence at
  0.62 to 0.70, though each spec carried the change.
- Review fix, 3 of 54: v5lf42 and 55, context chosen as fix (the
  configuration the other services read, and the status shape the other
  health checks report), and v5lf60, a disputed finding that proposes a
  remedy: Jev was uncertain at 0.53, the host fallback settled nothing, and
  the point's baseline, fix, was taken.

Retrieval, held out (B and D include the six injected-failure intents):

| Arm | n | Recall | Candidate recall | Bridge recall | p95 s | Host USD |
| --- | --- | --- | --- | --- | --- | --- |
| A | 126 | 0.847 [0.785, 0.904] | 0.847 | 0.417 | 0.02 | 0 |
| B | 132 | 0.838 [0.775, 0.896] | 0.838 | 0.417 | 19.7 | 3.14 |
| C | 126 | 0.847 [0.785, 0.904] | 0.868 | 0.417 | 0.03 | 0 |
| D | 132 | 0.850 [0.783, 0.908] | 0.850 | 0.833 | 20.3 | 2.92 |

- Retrieval p95 rose from v4's 5 s to 20 s. Jev left 173 of 425 coverage
  questions in B, and 172 of 413 in D, uncertain; the host settled 167 and
  163 of them, leaving 6 and 9 unresolved. On v4's held-out half it was 6 of
  75 (arm D), and on this calibration 4 of 83 (arm B).
  Between calibration (bc838d8) and held-out (21dff77) only the grader and
  the session's result capture changed, not retrieval; the questions and
  pages did. Why the fresh pages leave coverage uncertain is not diagnosed.
- Failure and cancellation: 12 of 12 as expected.
- Repetition: status the same in 8 of 8 groups; evidence the same, in order,
  in none (B and D), the same first three in 4 (B) and 3 (D), the same set in
  5 (B) and 4 (D). The lists hold 8 to 27 items; the first place that
  differs is the second in two groups of each arm, the third or fourth in
  most others.

Host spending per batch: calibration retrieval 3.8 min / USD 0.37, actions
0.6 / 0.09, answers 39.8 / 8.20, answers again 43.0 / 8.58; held-out
retrieval 28.7 / 6.06 (285 fallback turns), repetition 3.4 / 0.54, actions
3.0 / 0.45. USD 24.29 in all; Jev's cost has no dated price.

### v6 — `eval/jev/reliability-v6/` at 103521c

Frozen after label review r6 (see "Draft set"). Every run is at 103521c,
the merge of the frozen set, with the route change (#121) and L1 in place.
v6 has no action fixtures (A3), so decision quality and the three action
gates are not_measured, and v5's action results stand.

Calibration answers, `reliability-v6-calibration-answers` (not gated):

| Arm | Coverage | Unsupported, fact and abstain | Unsupported, all | Answer p95 s |
| --- | --- | --- | --- | --- |
| A | 0.992 | 0.180 | 0.191 | 17.2 |
| D | 0.970 | 0.063 | 0.106 | 21.4 |

Read as G2 reads held-out, that is a reduction of 0.65 and 4.2 s added.

Held-out runs: `reliability-v6-heldout`, `reliability-v6-heldout-answers`,
`reliability-v6-repeat`. The report is `reliability-v6-report.json`. The
four routing rate gates take the 95% Wilson interval and count each intent
once over B and D. The other rates (recall, bridge recall, coverage,
unsupported claims) and the paired differences take a 95% bootstrap
interval resampled by intent.

| Gate | Target | Value | Result |
| --- | --- | --- | --- |
| Deterministic integrity | 0 | 0 | pass |
| Graph benefit | 0.10 | 0.417 [0.25, 0.5] | pass |
| Overall recall | ≥ -0.02 | 0.029 [-0.008, 0.075] | pass |
| Answer support (G2: fact and abstain intents) | 0.25 | 0.359 | pass |
| Decision quality | 0.90 | — | not_measured |
| Added latency | ≤ 10 s | 4.33 s | pass |
| Operating ceiling | 0 | 0 | pass |
| Analysis recall | 0.90 | 1.0 [0.934, 1.0], n=54 | pass |
| Fact specificity | 0.90 | 0.987 [0.931, 0.998], n=78 | pass |
| Material classification | 0.90 | 0.974 [0.895, 0.994], n=58 | inconclusive |
| Request-only multipart classification | 0.90 | 1.0 [0.939, 1.0], n=59 | pass |
| Work-start, check and review-fix choice | 0.90 | — | not_measured |

Answers, held out (132 graded intents, none failed, cohort complete):

| Arm | Coverage | Unsupported, fact and abstain | Unsupported, analysis | Unsupported, all | Answer p95 s |
| --- | --- | --- | --- | --- | --- |
| A | 0.968 | 0.185 (470 claims) | 0.187 (460) | 0.186 | 22.0 |
| D | 0.968 | 0.119 (253) | 0.147 (468) | 0.137 | 26.3 |

- Answer support is (0.185 − 0.119) / 0.185 = 0.359. D's coverage on the
  fact and abstain intents is 0.026 below A's, interval [-0.074, 0.016],
  inside the -0.20 margin.
- The analysis intents, reported beside the gate and not gated: D's
  unsupported rate is 0.147 against A's 0.187, and its coverage is 0.037
  above A's, interval [0.0, 0.083].
- The pass does not rest on G2 alone. Over all 132 intents, the pooled gate
  v5 used gives (0.186 − 0.137) / 0.186 = 0.262, with a coverage
  difference of 0.0 [-0.032, 0.032].

The routing misses, each in both arms unless named:
- Fact specificity, 1 of 78: start-40 ("How should the new download handle
  large results?") was routed as analysis. All 12 routine cases that a
  standing policy covers were routed as facts.
- Material classification, 2 of 58: analysis-136 ("Board question: …")
  kept the quoted question as a request, and none-42 ("Prospect asks: …")
  did in D only. v5 missed the same shape (analysis-82, none-35). With two
  misses in 58 the lower bound stays under 0.90.

Retrieval, held out (B and D include the six injected-failure intents):

| Arm | n | Recall | Candidate recall | Bridge recall | p95 s | Host USD |
| --- | --- | --- | --- | --- | --- | --- |
| A | 132 | 0.896 [0.838, 0.942] | 0.896 | 0.417 | 0.03 | 0 |
| B | 138 | 0.877 [0.821, 0.929] | 0.877 | 0.417 | 7.2 | 0.83 |
| C | 132 | 0.896 [0.838, 0.942] | 0.917 | 0.417 | 0.03 | 0 |
| D | 138 | 0.897 [0.841, 0.944] | 0.897 | 0.833 | 9.1 | 0.42 |

- Retrieval p95 fell from v5's 20 s to 7–9 s (B 7.2 s, D 9.1 s, against
  19.7 s and 20.3 s). This is an observation, not attributed: the two runs
  differ in cases, corpus, route behavior and fallback policy, and #114's
  paired calibration left retrieval p95 unchanged ([answer-path.md](answer-path.md)).
- Coverage questions sent to the host fallback were 7 of 467 in D, against
  172 of 413 on v5's held-out. That counts fallbacks, not every uncertain
  Jev verdict: under L1 an analysis row's coverage questions get no host
  fallback. The 337 coverage questions of D's analysis-routed rows sent
  none, though 40 of those rows still end with a requirement marked
  uncertain (analysis-91: r0 at 0.23). The counters do not give the total
  uncertainty.
- Failure and cancellation: 12 of 12 as expected.
- Repetition: status was the same in 8 of 8 groups. Evidence was the same,
  in order, in 3 (B) and 2 (D).

Host spending per batch:
- Calibration answers: 39.1 min, USD 9.37.
- Held-out retrieval: 13.5 + 0.5 min, USD 1.25 (Jev's token ceiling
  stopped the first batch at 506 of 540 rows).
- Held-out answers: 46.3 + 40.0 + 4.9 min, USD 21.34 (two batches stopped
  at the USD 10 ceiling).
- Repetition: 3.1 min, USD 0.22.

USD 32.18 in all. Jev's cost has no dated price.

Exit on v6: both answer gates pass on fresh intents, and so do three of
the four routing gates. Material classification stays inconclusive, as it
was on v5. The work-start and review-fix choices stay at v5's inconclusive
result (A3). The stage's exit is therefore still not met. What remains is
the material cohort and the two action points.

### Limits

- Latency passes on v4 at 5.48 s. It failed on v3, narrowly, at 10.76 s
  against 10 s (v2: 19.3 s); v3's diagnosis, kept as the record behind v4:
  - The gate compares the p95 of each row's retrieval-plus-answer time: D
    29.95 s against A 19.20 s. Phase percentiles do not add up to it.
  - A row's host turns are of four kinds: drafting (one per draft, inside
    answer time), verification fallback (inside), retrieval fallback
    (inside), and the evaluation's grading turn (one per row, outside).
    A drafts once in every row.
  - The seven slowest D rows, about the slowest tenth:

    | Row | Drafting | Verification fallback | Retrieval fallback |
    | --- | --- | --- | --- |
    | start-22 | 2 | 2 | 0 s |
    | pasted-20 | 2 | 2 | 0 s |
    | analysis-19 | 1 | 0 | 3 turns, 15.7 s |
    | fix-23 | 2 | 2 | 1 turn, 4.0 s |
    | analysis-20 | 1 | 0 | 2 turns, 8.5 s |
    | pasted-22 | 2 | 1 | 0 s |
    | none-24 | 1 | 1 | 1 turn, 4.4 s |

    A second draft follows rejected claims. The analysis rows' retrieval
    fallbacks are coverage questions. A row makes at most two verification
    requests, and each falls back at most once.
  - The calibration answers showed the same tail, so the fix rested on
    calibration: v4, above, with fresh held-out evidence.
- Analysis/fact routing passes, but not perfectly, and its misses are one
  family in both sets: fact lookups phrased as advice, missed in both arms.
  v3: "What should I do if I lose my work laptop?" and "Which exit codes
  should it use?". v4: "What should I do with a suspected phishing email?"
  and "I'm creating a new service. What do I start from?". The analysis
  question does not yet separate this family. It is recorded, not tuned on.
- v2's figures below keep their own numbers, and v2's own limits stand for
  v2: it failed latency at 19.3 s and routing was inconclusive, at 3–4
  mistakes in 66 cohort rows per arm.
- v1's integrity failure was the scorer's: the fabricated-citation check read
  the dossier the answer began from, not the evidence each draft was given, and
  counted two citations of the user's pasted text; fixed in 252d0f9, so v1 and v2
  integrity are not measured the same way.
- v1 kept a dirty run: `reliability-heldout` ran from a dirty worktree and is
  kept as a record; the v1 report uses `reliability-heldout-clean`.
- The action gates rest on six held-out fixtures per point, and the fallback's
  host is the evaluation's default model; a different chat model shifts both
  host-checked answers and latency.
- Exit: on v4 every mandatory gate passed as first recorded, on labels
  reviewed before the held-out run and with the analysis path measured. The
  2026-10-10 re-judgment above makes routing and the three action gates
  inconclusive, so the exit is not met. The v4 labels were reviewed by a model
  (gpt-6.1-sol), not a human, at the repository owner's direction.
- Exit on v5: not met. Analysis recall, request-only multipart and check
  choice pass; fact specificity, material classification, work start and
  review fix are inconclusive. Their allowances under the Wilson rule are two
  misses in 72 and one in 56 or 54. Fact specificity missed 8 intents in both
  arms, six past; work start and review fix missed 3 each, two past. Material
  classification scores an intent's two arms as a ratio: analysis-82 failed
  in both and none-35 in B, 1.5 misses against one allowed. Answer support
  and added latency failed on the held-out answers at ac90615 (above).
  Fact specificity's misses are the advice-phrased family, the one diagnosis
  the data now supports. The answer path's changes since are in
  [answer-path.md](answer-path.md); "v6" below is the fresh test they need.

### English baseline for PR 9

`reliability-v4` at a0e88d3, report `raw/eval/jev/reliability-v4-report.json`,
gates v3, English input over English evidence. It replaces `reliability-v3` at
1e80cf5.

### Reproduction

A run's manifest records the code commit, and a folder refuses a run whose
manifest differs. Re-running a set therefore means a clean checkout of its
commit and the options recorded in each `run.json`: `cache cold`, `method
hybrid`, `k 8`, the default model and grader. Running the same commands at a
later commit measures a different manifest. Rebuilding a report from the
recorded folders works at any commit.

v6, from a clean checkout of 103521c:

```text
python tool/eval/compare.py raw/eval/jev/reliability-v6-calibration-answers --dataset eval/jev/reliability-v6/intents.json --split calibration --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v6-heldout --dataset eval/jev/reliability-v6/intents.json --split held_out --languages en --arms A B C D
python tool/eval/compare.py raw/eval/jev/reliability-v6-heldout-answers --dataset eval/jev/reliability-v6/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v6-repeat --dataset eval/jev/reliability-v6/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-91 analysis-92 analysis-94 pasted-43 pasted-48 route-42 memory-41 conflict-41
python tool/eval/report.py raw/eval/jev/reliability-v6-heldout raw/eval/jev/reliability-v6-heldout-answers raw/eval/jev/reliability-v6-repeat --out raw/eval/jev/reliability-v6-report.json
```

A batch stopped at a ceiling resumes when its command runs again on the
same folder.

v5. An `--estimate` writes the folder's manifest, so estimate into a scratch
folder, or delete the estimate's `run.json` before a run at another commit.
Calibration retrieval and actions, from a clean checkout of bc838d8 (the
first calibration answers ran there too, superseded by the second):

```text
python tool/eval/compare.py raw/eval/jev/reliability-v5-calibration --dataset eval/jev/reliability-v5/intents.json --split calibration --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v5-calibration-actions --experiment actions --dataset eval/jev/reliability-v5/intents.json --actions eval/jev/reliability-v5/actions.json --split calibration
```

The rest, from a clean checkout of 21dff77:

```text
python tool/eval/compare.py raw/eval/jev/reliability-v5-calibration-answers-2 --dataset eval/jev/reliability-v5/intents.json --split calibration --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v5-heldout --dataset eval/jev/reliability-v5/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v5-repeat --dataset eval/jev/reliability-v5/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-31 analysis-32 analysis-34 pasted-31 pasted-36 route-36 memory-35 conflict-35
python tool/eval/compare.py raw/eval/jev/reliability-v5-actions --experiment actions --dataset eval/jev/reliability-v5/intents.json --actions eval/jev/reliability-v5/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-v5-heldout raw/eval/jev/reliability-v5-repeat raw/eval/jev/reliability-v5-actions --out raw/eval/jev/reliability-v5-report.json
```

v4, from a clean checkout of a0e88d3. The calibration answers ran at 02fab78,
whose behavior manifest is the same; a rerun there needs its own checkout. The
actions use v2's fixtures:

```text
python tool/eval/compare.py raw/eval/jev/reliability-v4-calibration-answers --dataset eval/jev/reliability-v2/intents.json --split calibration --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v4-heldout --dataset eval/jev/reliability-v4/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v4-answers --dataset eval/jev/reliability-v4/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v4-repeat --dataset eval/jev/reliability-v4/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-25 analysis-26 analysis-28 pasted-25 pasted-30 route-30 memory-29 conflict-29
python tool/eval/compare.py raw/eval/jev/reliability-v4-actions --experiment actions --dataset eval/jev/reliability-v4/intents.json --actions eval/jev/reliability-v2/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-v4-heldout raw/eval/jev/reliability-v4-answers raw/eval/jev/reliability-v4-repeat raw/eval/jev/reliability-v4-actions --out raw/eval/jev/reliability-v4-report.json
```

v3, from a clean checkout of 1e80cf5. Calibration ran at ef45fec, whose
behavior is the same; a rerun there needs its own checkout. The actions use
v2's fixtures:

```text
python tool/eval/compare.py raw/eval/jev/reliability-v3-calibration --dataset eval/jev/reliability-v2/intents.json --split calibration --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v3-calibration-answers --dataset eval/jev/reliability-v2/intents.json --split calibration --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v3-heldout --dataset eval/jev/reliability-v3/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v3-answers --dataset eval/jev/reliability-v3/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v3-repeat --dataset eval/jev/reliability-v3/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-19 analysis-20 analysis-22 pasted-19 pasted-24 route-24 memory-23 conflict-23
python tool/eval/compare.py raw/eval/jev/reliability-v3-actions --experiment actions --dataset eval/jev/reliability-v3/intents.json --actions eval/jev/reliability-v2/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-v3-heldout raw/eval/jev/reliability-v3-answers raw/eval/jev/reliability-v3-repeat raw/eval/jev/reliability-v3-actions --out raw/eval/jev/reliability-v3-report.json
```

v2, from a clean checkout of 75dd67f:

```text
python tool/eval/compare.py raw/eval/jev/reliability-v2-calibration --dataset eval/jev/reliability-v2/intents.json --split calibration --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v2-heldout --dataset eval/jev/reliability-v2/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-v2-answers --dataset eval/jev/reliability-v2/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-v2-repeat --dataset eval/jev/reliability-v2/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-13 analysis-14 analysis-16 pasted-13 pasted-18 route-18 memory-17 conflict-17
python tool/eval/compare.py raw/eval/jev/reliability-v2-actions --experiment actions --dataset eval/jev/reliability-v2/intents.json --actions eval/jev/reliability-v2/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-v2-heldout raw/eval/jev/reliability-v2-answers raw/eval/jev/reliability-v2-repeat raw/eval/jev/reliability-v2-actions --out raw/eval/jev/reliability-v2-report.json
```

v1, from a clean checkout of 8588d03. That commit predates the fallback, so it
measures the historical policy:

```text
python tool/eval/compare.py raw/eval/jev/reliability-calibration --dataset eval/jev/reliability/intents.json --split calibration --languages en
python tool/eval/compare.py raw/eval/jev/reliability-heldout-clean --dataset eval/jev/reliability/intents.json --split held_out --languages en
python tool/eval/compare.py raw/eval/jev/reliability-answers --dataset eval/jev/reliability/intents.json --split held_out --languages en --arms A D --level answer
python tool/eval/compare.py raw/eval/jev/reliability-repeat --dataset eval/jev/reliability/intents.json --split held_out --languages en --arms B D --repeat 3 --ids analysis-01 analysis-04 analysis-07 conflict-09 memory-04 pasted-02 pasted-12 route-12
python tool/eval/compare.py raw/eval/jev/reliability-actions --experiment actions --dataset eval/jev/reliability/intents.json --actions eval/jev/reliability/actions.json --split held_out
python tool/eval/report.py raw/eval/jev/reliability-heldout-clean raw/eval/jev/reliability-answers raw/eval/jev/reliability-repeat raw/eval/jev/reliability-actions --out raw/eval/jev/reliability-report.json
```

A batch that stops at its threshold resumes when the same command runs again.

## v5 — enough cases to decide (design)

The 2026-10-10 re-judgment left four gates inconclusive because they rest on
too few cases, not because a case failed. v5 adds cases; it does not change a
target, and it is a new version, so v4 stays the record it is.

### How many

With a 95% Wilson interval, the held-out cases a 0.90 lower bound needs:

| Misses allowed | Cases needed |
| --- | --- |
| 0 | 35 |
| 1 | 53 |
| 2 | 69 |
| 3 | 84 |

v5 plans for one miss per gate: 54 held-out fixtures per action point and at
least 54 fresh intents in each routing cohort. Zero misses (35)
would be cheaper but turns any single slip into an inconclusive stage again.

### Action fixtures

A fresh `eval/jev/reliability-v5/actions.json` in the `jev-action-fixtures/1`
schema, written without reference to v2's, with labels kept balanced as v2's
are. Per point: 12 calibration and 54 held-out.

| Point | Held-out labels |
| --- | --- |
| `work.start` | 18 dispatch, 18 evidence, 18 clarify |
| `specs.check` | 11 each of `check:unit`, `check:docs-links`, `check:web-lint`, `check:schema`, `none` (55) |
| `loop.fix` | 27 fix, 27 context |

### Routing: a balanced cohort and per-class gates

v4's routing cohort is 66 held-out intents, of which 3 are analysis and 63
fact. Answering "fact" every time would score 0.955, so the single
accuracy says little about recognising analysis; v4's runs got all 3 right
and called 2 fact intents analysis. Segment classification is similar: 57 of
the 66 are one segment that is a request.

v5 judges each class on its own. That changes the gates (gates file version
5); the owner approved it on 2026-10-10, with one miss allowed per gate.
Both segment gates score an intent's complete labelled segment vector,
requests included:

| Gate | Cohort (fresh held-out, `route_expected`) | Target |
| --- | --- | --- |
| `analysis_recall` | intents labelled analysis | lower bound ≥ 0.90 |
| `fact_specificity` | intents labelled fact | lower bound ≥ 0.90 |
| `material_classification` | intents with at least one material segment | lower bound ≥ 0.90 |
| `request_classification` | intents of two or more segments, every one a request | lower bound ≥ 0.90 |

The design's first review added `request_classification`: with only the
first three, a router could drop the first request of every request-only
multipart query and still score 1.0 on all of them. Keeping
`analysis_routing` and `segment_classification` and only adding cases was
cheaper and was declined: a gate that a constant answer passes cannot show
that the router tells the two apart.

Acceptance rests on fresh intents only, across the existing families, with
the pages they name, labelled before any v5 run: at least 54 analysis and 54
fact, and among them at least 54 with a material segment and 54 request-only
multipart, so at least 108. v4's held-out half was revealed, so it cannot carry
the next generalization claim (see the exit protocol above). It becomes the
regression set: run beside v5 and reported under v4's gates, but counted in
no acceptance interval.

### Process

1. Draft the fixtures and intents, freeze them with the gates, and record
   hashes, as for v2–v4.
2. Label review by the reviewer cell (gpt-6.1-sol) until a round finds
   nothing, before any held-out run. A model review, at the owner's direction,
   as before.
3. Calibration, then held-out: arms, answers, repetition and actions, on the
   current code. v4's spending scaled to the larger set is roughly USD 20.
4. Report with the Wilson intervals. Every mandatory gate passing closes
   step 3 and gives PR 9 its English baseline; a failure gets a diagnosis and
   a next version, never an edited gate.

### Frozen

`eval/jev/reliability-v5/`, frozen 2026-10-10 after review r5 (two full-set
rounds by gpt-6.1-sol; the second found nothing); `tool/test_reliability_v5.py`
holds the counts below.
- `intents.json`: v4's 72 calibration intents verbatim, v4's corpus whole,
  and 132 held-out intents with 132 pages only they name. The families are
  v4's: analysis 60 (54 analysis, 6 facts phrased as advice), pasted 12,
  failure 6, the other nine 6 each. Routing cohorts: 54 analysis, 72 fact,
  56 with a material segment, 56 request-only multipart.
- `actions.json`: 199 fixtures, 12 calibration and 54 held out per point
  (`specs.check` 55), labels balanced.
- `gates.json`: version 5, v4's gates with routing replaced by the four
  per-class gates; `report.routing_report` scores all four.

Each new page was checked against v4's corpus by TF-IDF similarity, and
topics whose facts an existing page already stated, or contradicted, were
replaced. One near miss is kept on purpose: `repo/docs/release.md` names CLI
build targets, which do not answer the missing intent about build agents.

Round 1 of the label review found that work-start file locators gave every
label away (`:1` only on evidence fixtures), so locators, goal paths and
choosing verbs were spread over both labels, and review-fix remedies and
rule-sounding words over theirs. No single surface rule now predicts more
than 25 of 44 work-start or 38 of 66 review-fix labels, except "disputed",
which means context by the decision prompt itself. Every bridge target shares
no content word with its query, and graph-off retrieval misses all six.

### Calibration at bc838d8

Retrieval, actions and A/D answers on the calibration split, USD 8.66.
Routing was right on every calibration row (B and D, 132 of 132); actions
12/12 work-start, 12/12 check choice, 9/12 review-fix (two uncertain, both
context labels the model sent as fix).

Answer support fell to 0.141 (A unsupported 0.307, D 0.264) against v4's
0.736 on the same 72 intents (A 0.423, D 0.112). The cohort is incomplete:
`memory-07:en:A:0` has an `answer_error` (WinError 32 while closing the
grading session) and no grade, so the figure is a diagnostic over 65 A and
66 D rows, not acceptance evidence. The cause is #59, after v4
ran at a0e88d3: ordinary answers now synthesize as `unverified`, and the
claim validator runs only with `verify_claims=True`. The runner's arm D
follows the product path, so it no longer withholds unestablished claims;
D's claims rose from 179 to 501. The owner chose to keep measuring the
product path. Part of the drop is the grader's: the answering host may read
the repository with Read, Glob and Grep, while the grader saw only the
dossier, so a true claim from a page the host read was graded unsupported
(roughly 11 of D's 132 and 23 of A's 142). The grader now receives
`read_passages`: every tool result the host received, whole, collected by the
session itself for both providers (`ChatSession(results=...)`), since a tool
event shows only the first 4000 characters and Codex `exec` sent no result
event at all. Calibration answers run again, complete, before the held-out
half.

## v6 — a fresh test of the changed answer path (design)

Since v5 froze at bc838d8, the grader (#109), the answer path (#112, #113),
retrieval (#114) and analysis coverage (#118, L1) changed, each diagnosed on
v5's calibration or held-out answers. v5 can no longer test them. Its exit
is not met: both answer gates failed at ac90615, and fact specificity,
material classification, work start and review fix are inconclusive. The
L1 rerun at 01702da (added latency 0.283 s, answer support 0.230) is on a
revealed cohort, and it records that latency passed there and support did
not.

v6 is a new version with fresh held-out intents. Three things are decided
before any v6 run, because each changes what v6 is built to measure.

### Answer-support cohort

v5's held-out answer cohort was 43% analysis (54 of 126 graded intents),
because the routing gates needed 54 analysis intents; calibration's was 5%
(3 of 66). Analysis answers are synthesized and never checked, and on v5's
held-out D's analysis rate matched A's: 0.164 against 0.161 at ac90615,
0.168 against 0.180 under L1. On the other intents D was 22.7% below A at
ac90615 and 53.4% below under L1. The 8 fact intents the route sent to
analysis carried 13 of D's 31 unsupported fact claims at ac90615, published
unverified on that path; how many a right route would have avoided is for
the route change's own measurement.

| Option | Gate | Effect | Cost |
| --- | --- | --- | --- |
| G1 | v5's pooled gate, unchanged | No gate change | Its value depends on the cohort's analysis share, which the routing gates set |
| G2 | Answer support alone narrows: both its unsupported-claim rate and its coverage interval read intents labelled fact or abstain, by label, not by route, with the target (0.25), the coverage margin (−0.20) and the complete-cohort rule unchanged. Added latency and integrity keep the whole answer workload, analysis included. Analysis intents' unsupported and coverage rates are reported beside the gate, ungated | It measures what verification can change, and an intent the route wrongly sends to analysis still counts against it | A gate change, chosen after v5's results; a fresh held-out run tests whether it generalizes, but cannot make the choice independent of them. A v6 pass claims less: support on fact and abstain answers only |
| G3 | Analysis claims are graded as repository-fact premises or inference, and premises count | Reaches the analysis claims a passage can support | A new grader classification with its own label review; unmeasured (answer-path.md, S1) |

Narrowing added latency with answer support would remove the problem L1
addresses instead of testing it: rebuilt from ac90615's recorded rows,
added latency is 17.49 s over every graded intent and 2.37 s over the fact
and abstain intents alone.

### Route: advice the repository states

The `ANALYSIS` route question names advice as analysis. The fact intents it
misses are advice the repository states, "What should I do if I
accidentally commit an API key?" among them, the same family in v3, v4 and
v5, recorded and not tuned. v5 missed 8 of 72 (0.889 [0.796, 0.943]). If
that rate persists, adding cases alone will not lift the lower bound to
0.90; fresh cases need not keep it, but three versions have missed the same
family.

The change to test: reword `ANALYSIS` so that a procedure or recommendation
the repository's sources state is a fact, not analysis. The current text
already contrasts the assistant's own working-out with facts the sources
state, and names advice on the analysis side; whether a sharper line helps
is what the change measures, with analysis recall protected. Every v3–v5 case is
revealed and may be used to calibrate it, and analysis recall must hold on
v5's 54 analysis intents. No fitted policy rule exists for the `analysis`
kind (`eval/jev/policy.json`), so the new question's digest unbinds nothing.
It is its own PR, reviewed and measured on calibration before v6 freezes.

### Action points

Work start and review fix each missed 3 of 54. The cases a 0.90 lower bound
needs, by misses allowed, extend v5's table: 4 misses 99, 5 113, 6 127, 8
153, 9 166. A fixed allowance is not a fixed rate: if each fixture misses
with v5's 3/54, 166 fixtures pass about 56% of the time, and an 80% chance
of passing needs about 300 (binomial, as a sizing illustration, not a
forecast).

| Option | Work | Cost |
| --- | --- | --- |
| A1 | Write and review about 300 fresh fixtures per point for the two points, unchanged decisions | Label review of about 600 fixtures; runs are cheap (v5's 163 cost USD 0.45) |
| A2 | Diagnose the six misses and change the two decisions as their own versioned policy change, then 54 fresh fixtures per point, which allow one miss | Two diagnoses and changes before v6, each needing to do better than v5's three misses, not only to be new |
| A3 | Leave both at v5's inconclusive result; v6 measures the answer path and routing only | The stage's exit stays unmet until a later version settles them |

### Held-out set

Fresh intents with pages only they name, labelled and reviewed before any
v6 run, as in v5's process. v5's held-out half becomes calibration and
regression data. Per v5's sizing, one miss allowed per routing gate: at
least 54 fact intents, of which at least 12 are advice the repository
states; 54 analysis intents, since a reworded route question needs fresh
analysis recall; and the material and request-only multipart cohorts at 54
each, drawn from the same intents. Under G2 answer support reads the fact
and abstain intents, v5's scale; the answer run still covers every intent,
since added latency reads them all.

v5's held-out spending, to scale from: retrieval USD 6.06, answers 24.83
(D's analysis rows cost about USD 0.17 each), repetition 0.54, actions 0.45;
calibration answers 9.50 at 01702da. v6 without A1 comes to roughly USD 40,
calibration included.

### Recommendation

G2, with the analysis rates reported beside it. The route change, as its own
PR, before v6 freezes. A3 for the action points, so v6 stays on the answer
path; A2 follows as its own version. The owner decides each, since G2 and
the route change alter what a v6 pass means.

The owner chose all three on 2026-10-11: G2, the route change as its own
PR, and A3.

### Route change, calibrated on revealed cases

A probe sends the route request as `Flow.route` builds it for the
benchmark's setting (original English, empty current state, no omitted
context, the four source families, retrieval not required by the caller),
with the `ANALYSIS` text swapped, for every v3–v5 intent with `route_expected`,
deduplicated by English text: 324 cases, 63 analysis and 261 fact, all
revealed. Jev answers each; a score of 0.8 or more is yes, as the
provisional policy has it, and a score between 0.2 and 0.8 is uncertain,
which production sends to the host. The probe reproduces v5's held-out
misses: all 8 fact intents score at least 0.82 with today's text. Its
scripts, wordings and results are in `raw/eval/jev/route-probe-v6/`; Jev
only cost about 290,000 input tokens per wording, and the host turns below
USD 0.84 in all.

| Wording | Analysis answered yes | Facts answered yes | Facts uncertain |
| --- | --- | --- | --- |
| Today's | 63/63 | 12/261 | 44 |
| V1: advice that a rule or convention covers is a fact | 51/63 | 0/261 | 21 |
| V2: advice that no source states | 62/63 | 6/261 | 35 |
| V3: a plan or diagnosis for the team's own problem is analysis; what to do in a routine case a standing policy would cover is a fact | 63/63 | 0/261 | 27 |
| V4: V3's line, under the word advice | 61/63 | 0/261 | 30 |

A second stage sends each initially uncertain case through
`decision.decide` with the host fallback, as production does; it asks Jev
afresh, and the host only what that answer leaves uncertain. Today's text
then sends 13 of 261 facts to analysis: `start-30`, 0.78 at first, scored
yes on the second Jev answer, with no host turn. The host was asked about
analysis in 39 of its 44 cases: it said no in 37, and its turn failed in 2
(`memory-03`, `fix-30`), which stayed uncertain and which production reads
as not analysis. Under V3 the host was asked in 26 of 27 and said no each
time, and the second Jev answer settled `memory-27` as no, so V3 sends
none. Its margins are narrow:
the lowest analysis scores 0.81 and the highest fact 0.77 (`start-35`).
V3 is fitted to exactly these cases, so the numbers show it separates them,
not that it generalizes; that is v6's held-out question, beside analysis
recall on fresh analysis intents. The probe kept only the analysis answer.
The same request asks retrieve, progress, source and ask questions, whose
texts are unchanged but which Jev reads beside the new one, and other
production settings (a current state, a narrower source set, retrieval
already required) change its input; neither is measured here. v6's runs
measure the sibling questions on fresh requests in the benchmark's setting
only: no v6 intent carries a current state, a narrower source set or a
caller-required retrieval, and the arms harness passes none, so those
settings stay outside v6 and need their own measurement.

### Draft set

`eval/jev/reliability-v6/intents.json` keeps v5's 72 calibration intents
verbatim and adds 138 fresh held-out intents (review r6), with pages only
they name over v5's corpus, no earlier page edited. The analysis family has
66: 36 request-only multipart and 18 with material, all analysis, and 12
routine cases a standing policy covers, labelled fact (4 single, 4
multipart, 4 with material). The other families keep v5's sizes: 12 pasted
and 6 each for the rest, the failure family's faults 2 cancelled, 2
exhausted and 2 unavailable. Among the 132 routed intents: 54 analysis, 78
fact, 58 with a material segment, 59 request-only multipart.
`eval/jev/reliability-v6/gates.json` is v5's gates with G2. Under A3 v6
has no action fixtures, so its action gates and decision quality are
not_measured, and v5's action results stand. `tool/test_reliability_v6.py`
pins these counts. Two review rounds by gpt-6.1-sol (r6) preceded the
freeze on 2026-10-11. Round 1 found three problems, all fixed:
- Two analysis intents asked a separate cost question with no answer part.
  Each now has one, and thirteen other analysis references now cover a
  second request.
- The report did not show the analysis intents' coverage beside G2. It
  now does.
- This page claimed v6 measures other production settings. That claim is
  narrowed above.

Round 2 found nothing. No v6 held-out case has been run.

## v7 — material and the two action points (design)

v6 left three parts of the exit open:
- material classification, inconclusive at 2 misses in 58;
- work start, still at v5's 3 misses in 54;
- review fix, also still at v5's 3 misses in 54.

Every other gate passed on fresh cases. v7 is designed to settle these
three. The answer path is not changed, unless a change below alters
what it reads.

### Material: a forwarded question

Every material miss recorded on a held-out set is one shape: a question
someone else asked, quoted, and followed by the person's own request.

| Set | Material rows (B and D) | Quoted questions among them | Misses |
| --- | --- | --- | --- |
| v3 | 12 | 0 | 0 |
| v4 | 12 | 0 | 0 |
| v5 | 112 | 12 | 3 |
| v6 | 116 | 12 | 3 |

The misses are listed below.
- v5:
  - analysis-82 ("Board question: …"), in both arms.
  - none-35 ("Customer asks: …"), in B.
- v6:
  - analysis-136 ("Board question: …"), in both arms.
  - none-42 ("Prospect asks: …"), in D.

All 228 rows without a quoted question were right, and 18 of the 24 with
one. The `ASK` question names
pasted notices, documents and messages, and instructions written for
someone else, but not a question someone else asked. When the person then
asks about that question's subject, the router counts the quoted question
as a request too. A miss adds the quoted question as one more requirement
that the evidence is checked against. In analysis-136 that is close to
what the person wants answered; in none-42 it is not.

| Option | Change | Effect | Cost |
| --- | --- | --- | --- |
| M1 | Reword `ASK` so that a question quoted from someone else is material, even when the person's own request is about the same subject. Calibrate it, as #121 did, on every revealed v3–v6 segment, with request classification protected | Targets the one recorded shape | A wording fitted to six revealed rows; a fresh set must test it |
| M2 | Relabel: a forwarded question that the person's own request asks the assistant to answer counts as a request | Removes the disagreement by convention | A label change chosen after the failures, which moves the measure, not the router |
| M3 | Neither; more material cases, with forwarded questions kept at their share | The router is unchanged; the gate may settle on sample size alone. At v6's rate (113 of 116 rows, 0.974) the Wilson lower bound reaches 0.921 at 100 intents, and 97 of 100 gives 0.916. These figures are sizing assumptions, not forecasts | Repairs nothing, and fresh cases may miss at another rate. It needs no answer run, since the router does not change |

### Action points: A2

The six v5 misses fall into three patterns. The fixtures are in
`eval/jev/reliability-v5/actions.json`, and the rows are in
`reliability-v5-actions`.

- Work start, 3 of 54: v5ws25, 27 and 29, labelled dispatch.
  - Each specification names its files and has concrete acceptance
    criteria, with no rules or decisions. Examples: "Add a --limit option,
    default 100, to the list subcommand in src/cli.py", and "Delete
    legacy_hash() from src/auth/hashing.py; nothing calls it".
  - Jev chose evidence at 0.62, 0.70 and 0.66. The prompt sends the turn
    "when the specification already carries what the work needs"; Jev
    read these specifications as depending on repository facts the
    grounds did not name.
- Review fix, convention findings: v5lf42 and 55, labelled context.
  - Each finding points at how the rest of the repository does it: "the
    configuration the other services read", and "the other checks report
    a status object".
  - Jev chose fix at 0.72 and 0.89. The prompt sends the findings as they
    are "when each already says what to change"; Jev read them as saying
    so.
- Review fix, the disputed finding: v5lf60, labelled context.
  - The finding was disputed in round 1, and the prompt names an earlier
    dispute as a reason for context.
  - Jev was uncertain at 0.53. The host fallback was asked once and
    settled nothing, so the baseline, fix, was taken. Why it settled
    nothing is not diagnosed.

The changes to test:
- W: sharpen the work-start prompt. A specification that already states
  the behavior or decision required carries what the work needs, when no
  unresolved repository fact, rule or convention is needed to choose it.
  Naming files and checks alone does not override the evidence condition.
  v5ws05 (pick the catalogue's cache lifetime) and v5ws31 (choose the
  minimum password length) name both, and stay evidence. They are kept as
  counterexamples during calibration.
- F: sharpen the review-fix prompt. A finding that appeals to how other
  parts of the repository do something turns on a convention, even when
  it proposes a remedy.
- F2: diagnose v5lf60's unsettled fallback. Replayed at 6b8149b, the
  host's turn answered `context`, the label, but put a sentence before
  the JSON object ("The finding was disputed in an earlier round, so the
  rule says gather context."). `knowledge.parsed` takes bare JSON or a
  fenced block only, so the turn failed with `JSONDecodeError` and
  settled nothing. The fallback prompt asks for no prose, and the host
  did not comply. The fix belongs in `parsed`, which the host fallback,
  the answer grader and two other readers share. The recorded rows keep
  fallback counts only, so how often this happened before is not known.

The revealed fixtures calibrate W and F, with the other labels protected:

| Set | Work start | Review fix |
| --- | --- | --- |
| v1 | 12 | 12 |
| v2 | 12 | 12 |
| v5 | 66 | 66 |

No fitted action policy exists (`eval/jev/action-policy.json`), so a new
prompt digest unbinds nothing. v5's 163 action fixtures cost USD 0.45 to
run.

### Set and cost

A fresh v7 set has two parts.
- Intents:
  - at least 54 with a material segment, at least 12 of them a forwarded
    question;
  - enough request-only multipart, analysis and fact intents to keep
    request classification, analysis recall and fact specificity measured
    under the reworded `ASK`.
- Action fixtures: 54 fresh ones for work start and 54 for review fix,
  each split evenly between its labels.

Gates are v6's. The answer run belongs in v7 only if M1 is chosen: a
changed `ASK` changes which segments the evidence is checked against.

Estimated cost, from v6's spending:
- Retrieval: about USD 1.3.
- Actions: under USD 1.
- Answers, if run: about USD 21.
- Calibration answers: about USD 9.

### Recommendation

M1, with W, F and F2, as one PR, calibrated on revealed cases and
reviewed. Then a fresh v7 set with its label review, and the runs above
on the owner's approval of their cost. M2 is not recommended, because it
changes the measure after seeing the failures. The owner decides.

The owner chose M1 with W, F and F2 on 2026-10-12. M1 was confirmed
after M3's sizing was corrected in review.

### Implementation, calibrated on revealed cases

F2: `knowledge.parsed` still reads bare JSON or a fenced block first.
Failing that, it reads one answer after prose. That answer is the first
whole JSON object outside any open brace, with nothing after it except a
closing fence. Some text still fails:
- text with no such object;
- a second answer after the first;
- an inner object of a cut-off one.

Its four readers share the change.

W and F replace the two prompts in `decisions.PROMPTS` with the wording
above. The revealed work-start and review-fix fixtures of v1, v2 and v5,
168 once duplicates are removed, were replayed through
`decisions.propose`:

| Prompts | Work start | Review fix | Host fallbacks that failed to parse |
| --- | --- | --- | --- |
| Before | 81 of 84 | 78 of 84 | 2 of 47 |
| W, F and F2 | 84 of 84 | 83 of 84 | 0 of 35 |

The one miss left, v5lf09 (context, Jev chose fix at 0.64), was missed
before too. v5ws05 and v5ws31 stay evidence.

M1 was calibrated on the 255 distinct multi-segment route intents of
v3–v6: 132 with a material segment and 123 request-only. Each `ASK` draft
was scored by Jev alone, under the route's thresholds (no at 0.2 or below,
yes at 0.8 or above) and `Flow.route`'s rule for which segments are kept.
A case whose vector turns on an uncertain segment goes to the host, which
reads the same `ASK`. Those cases were run again through
`decision.decide`, with the host fallback, so Jev judged them a second
time first.

| `ASK` | Material, settled by Jev | Material, run again with the host | Request, settled by Jev | Request, run again |
| --- | --- | --- | --- | --- |
| Before | 122 right | 6 of 10 right | 123 right | 0 |
| Adopted | 114 right | 18 of 18 right | 123 right | 0 |

Before the change, this probe's second pass marked four quoted parts as
asked:
- the quoted questions in analysis-73, analysis-82 and analysis-136;
- the quoted task in start-36.

These are probe results, not a replay of earlier runs. The held-out misses
recorded earlier were analysis-82 and none-35 on v5, and analysis-136 and
none-42 on v6. They have the same shape. In this probe, none-35 and
none-42 were right before the change, and analysis-73 and start-36 had
been right on v5. The adopted wording
adds "or a question someone else asked that the user quotes, even when the
user then asks about the same subject". Jev still scores no quoted question
at 0.2 or below. It does score them lower, and the host settles them.

Four other drafts were rejected. Each one either asked whether a question
was in "the user's own words", or added the clause as a sentence of its
own. Every one lowered Jev's confidence in the person's own question too,
so between 25 and 51 cases went to the host, with up to 4 of them
request-only. On these cases the adopted wording leaves 8 more cases
uncertain than before. If each costs a host call, that is about USD 0.12
more, at the USD 0.011–0.018 a priced call cost here. This is an
estimate. The probe did not record whether each second pass reached the
host, so 3 of the 18 with no price may have settled on Jev's second
judgment or may have gone unpriced. Each case ran once, and a fresh set
must test both the wording and the host.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Freeze | Freeze new English labels, versions and gates | Done |
| 2 | Run | Run calibration, held-out comparison and repetitions | Done |
| 3 | Publish | Publish current baseline and unresolved limits | In progress — v4 baseline published; re-judged with Wilson intervals, routing and action gates inconclusive (too few fixtures) |
| 4 | v5 | Enough held-out cases for the rate gates, per-class routing | In progress — held-out retrieval, repetition and actions run (Results, v5): 3 rate gates pass, 4 inconclusive; held-out answers at ac90615 fail both answer gates (answer-path.md) |
| 5 | v6 | Fresh held-out test of the changed answer path | In progress — owner chose G2, the route change and A3; route change calibrated on revealed cases (#121); gates v6 and 138 fresh held-out intents reviewed (r6) and frozen on 2026-10-11 (Draft set); run at 103521c (Results, v6): both answer gates and three routing gates pass, material classification inconclusive; work start and review fix stay at v5's inconclusive result (A3) |
| 6 | v7 | Material classification and the two action points | In progress — designed (v7 design); owner chose M1 with W, F and F2; implemented and calibrated on revealed cases (Implementation); fresh set next |

## Sources

The [existing evaluation plan](../jev/10-evaluation-rollout.md) records historical
results and the rationale for gate version 3.
