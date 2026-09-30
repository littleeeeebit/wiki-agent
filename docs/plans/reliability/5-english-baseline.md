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
(`gates.json` byte-identical across them). The runs live under `raw/eval/jev/`,
which git ignores; the reproduction commands below rebuild them.

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

Rerunning the eight calibration rows that drafted twice under v3 showed that
6 of the 8 second drafts had this cause: pasted-01, 04, 06, 07, 08 and 09. The
prompt now says to name only the ids `requirements` lists, which need not
start at `r0`. The same six rows then drafted once, with no `unknown_requirement`.

On the whole calibration half (`reliability-v4-calibration-answers`, at
02fab78), against v3's calibration run:
- added latency fell from 8.10 s to 6.25 s (D p95 29.15 s to 26.86 s);
- answer support rose from 0.687 to 0.736.

The bound with no second draft at all was 5.72 s, so nothing else was changed.
A cap on host coverage for analysis rows, or one host verification turn per
answer, would have cut about 0.4 s each on calibration. Neither is in v4.

The v4 set keeps the same calibration half and adds 72 held-out intents
written after v3's reveal (review r4, gpt-6.1-sol, two full-set rounds;
approved input intents `b96ad318`). Two earlier rounds were answered by
gpt-6-luna and are not counted: the reviewer cell had been launched with a
pinned model. The action fixtures are v2's, unchanged.

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
- Exit: on v4 every mandatory gate passes, on labels reviewed before the
  held-out run and with the analysis path measured, which is what the
  decision protocol requires. The v4 labels were reviewed by a model
  (gpt-6.1-sol), not a human, at the repository owner's direction.

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

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Freeze | Freeze new English labels, versions and gates | Done |
| 2 | Run | Run calibration, held-out comparison and repetitions | Done |
| 3 | Publish | Publish current baseline and unresolved limits | Done — v4 baseline; every mandatory gate passes (latency 5.48 s) |

## Sources

The [existing evaluation plan](../jev/10-evaluation-rollout.md) records historical
results and the rationale for gate version 3.
