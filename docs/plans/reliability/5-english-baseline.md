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
off so the route request is not served from cache, and patch `Flow.evaluate` for
the named phase to raise what `test_decision_flow` already maps to each status —
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

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Freeze | Freeze new English labels, versions and gates | Not started |
| 2 | Run | Run calibration, held-out comparison and repetitions | Not started |
| 3 | Publish | Publish current baseline and unresolved limits | Not started |

## Sources

The [existing evaluation plan](../jev/10-evaluation-rollout.md) records historical
results and the rationale for gate version 3.
