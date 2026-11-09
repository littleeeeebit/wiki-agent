# Spec views — main-2: the main experiment

Outcome: **further verification needed**. Candidate: condition D — fixed from the pilot report: D, the narrowed agent JSON, was the only condition not below A on verified success (0.500 against A's 0.500; B and C 0.417) at nearly the same cost per verified success (USD 1.651 against 1.633). Chosen by the owner on 2026-11-08 before any main-run cell.

Implementer `claude-opus-5-5` at effort `high`, the pilot's harness, tools, retries, limits and isolation; 10 held-out tasks, 1 repetitions fixed-spec and 1 in the pipeline. Config digest `dd257e2d2e9a`. Fixed-spec spent USD 50.2197 in 4.395 h (ceiling USD 55, 7 h); pipeline (`main-2-pipeline`) USD 32.8169 in 3.195 h (ceiling USD 37, 5.5 h).

main-2 narrows main-1 to the candidate D against A. The owner stopped main-1 on 2026-11-09 after 10 of its 80 cells (journal `raw/eval/spec-views/main-1.journal.jsonl`, kept as it is and never pooled here). main-2 runs the same ten held-out tasks and frozen specs on a fresh journal on harness commit c09ab993aae5, whose work-spec rules (`tool/prompts/work-spec.md`, the first part of every condition's system prompt) carry four lines #313 added after main-1 froze, on pinning prerequisite-review evidence by hash. A and D receive the identical text, so D-A holds it equal; absolute rates are not the pilot's prompt's. The model, effort, tools, retries, limits and isolation are the pilot's.

## Three judgments

| Stage | Result | Grounds |
| --- | --- | --- |
| Implementation pass | pass | `-m pytest -q -p no:cacheprovider -n 0 tool/test_spec_views.py` at `58a359a77bd8`: passed; `-m pytest -q -p no:cacheprovider -n 0 tool/test_spec_views.py` at `58a359a77bd8`: passed |
| Experiment validity | valid | config yes, one_code_commit yes, held_out yes, frozen_inputs yes, costs_recorded yes, all_cells_ran yes |
| Adoption, fixed | further verification needed | neither path passes and the intervals still allow one; difference -0.100 [-0.300, +0.000], ratio 1.211 [0.827, 3.853]; critical regressions 1 against A's 2, none forbidden or deleting |
| Adoption, pipeline | further verification needed | neither path passes and the intervals still allow one; difference +0.000 [-0.300, +0.300], ratio 1.234 [0.397, 3.157]; critical regressions 1 against A's 1, none forbidden or deleting |

Adoption needs fixed and pipeline to pass. Each comparison is the candidate D against A, success as a difference in rate and cost as the ratio of cost per verified success. Quality path: difference at least +0.05 with the 95% interval's lower end above 0, and the ratio's upper end at most 1.10. Cost path: the difference's lower end at least -0.02, the ratio at most 0.85 (15% cheaper per success) and its upper end below 1. A comparison is rejected when a critical regression blocks it or its intervals exclude both paths (difference upper end below +0.05 or ratio lower end above 1.10, and difference upper end below -0.02 or ratio lower end above 0.85); otherwise, when neither path passes, it needs further verification. These are the operating defaults the request gave, since no product criterion for spec quality is recorded in this repository; none was derived from the pilot's results. Adoption also needs implementation pass and experiment validity.

## Comparisons

Per task, the mean success over repetitions of one condition minus the other; the cost ratio divides the first condition's cost per verified success (every attempt and every spec call it needs) by the second's. Intervals resample tasks with their repetitions together (95% percentile bootstrap); a resample without a success for the first condition counts as an infinite ratio.

| Comparison | Held equal | Success difference [95% CI] | Cost-per-success ratio [95% CI] |
| --- | --- | --- | --- |
| D-A | the final candidate against A, frozen specs | -0.100 [-0.300, +0.000] (n=10) | 1.211 [0.827, 3.853] (n=10) |
| pipeline D-A | the final candidate against A, each repetition generated afresh from the original text | +0.000 [-0.300, +0.300] (n=10) | 1.234 [0.397, 3.157] (n=10) |

No significant difference is not evidence of equivalence: an interval that spans zero says the sample cannot tell the conditions apart.

## Fixed specs

| Condition | Cells | Successes | Success rate [95% CI] | Hidden tests passed | Regressions | Critical | Missing critical conditions | Unsupported policy additions | Unnecessary holds | Infra reruns | Tokens | Wall h | USD incl. spec calls | USD per success |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 10 | 4 | 0.400 [0.100, 0.700] (n=10) | 0.767 | 2 | 2 | 2 | n/a | n/a | 0 | 20513840 | 3.72 | 16.4944 | 4.1236 |
| D | 10 | 3 | 0.300 [0.100, 0.600] (n=10) | 0.733 | 1 | 1 | 3 | 0 | 0 | 0 | 17673943 | 3.70 | 14.9831 | 4.9944 |

## Full pipeline (`main-2-pipeline`)

Each repetition generates a fresh spec from the pull request's original text with the `next` prompt (setting off), then enriches it and selects D's requirements, as the pilot's preparation did once. A runs on that repetition's spec brief and D on its narrowed agent view; one generation is shared by the pair, so the comparison carries generation variance on both sides. Every repetition's spec calls count toward its condition's cost; nothing is cached. Journal: `raw/eval/spec-views/main-2-pipeline.journal.jsonl`.

| Condition | Cells | Successes | Success rate [95% CI] | Hidden tests passed | Regressions | Critical | Missing critical conditions | Unsupported policy additions | Unnecessary holds | Infra reruns | Tokens | Wall h | USD incl. spec calls | USD per success |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 10 | 4 | 0.400 [0.100, 0.700] (n=10) | 0.500 | 1 | 1 | 1 | n/a | n/a | 0 | 14234002 | 3.55 | 15.8338 | 3.9585 |
| D | 10 | 4 | 0.400 [0.100, 0.700] (n=10) | 0.500 | 1 | 1 | 1 | 0 | 0 | 0 | 16228078 | 3.68 | 19.5408 | 4.8852 |

Measures: **requirement satisfaction** — the fraction of a cell's hidden tests (the pull request's own tests of what it asked for) that passed, averaged over cells. **regressions** — cells whose affected existing tests, as the merge base wrote them, failed. **critical regressions** — cells meeting `critical_regression`. **missing critical conditions** — failed cells that changed non-test code, kept every affected test passing and still failed a hidden test: an implementation that missed a condition the request's tests check. **unsupported policy additions** — requirements in the specs a condition ran whose `source.kind` is `policy` and whose status is `assumed` or `undecided`: rules the enrichment added without a confirmed ground. A has none (no requirements). **unnecessary holds** — specs on which `spec_views.blocked` names a dependency on an undecided requirement, so production's first turn would have held for a person, although the merged pull request shows the task was answerable without one. **retries** — per-cell retries (fixed at 0) and infrastructure reruns. **cost per success** — every attempt's USD (a cost the CLI never reported at its cap) plus every spec call the condition needs, divided by verified successes; the ratio's interval resamples tasks.

## Representative failures

From the journal, up to two final rows per failure cause.

- fixed pr125/A1 — verification: hidden {'tool/test_decision_flow.py::test_text_with_no_single_answer_that_ends_it_settles_nothing': 'failed'}; affected failing none; 5 paths changed; no done-report; 343.3 s, USD 1.1332012
- fixed pr191/A1 — verification: hidden {'tool/test_loop_progress.py::test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose': 'failed'}; affected failing none; 5 paths changed; claimed done; 1455.9 s, USD 0.9678243999999997
- fixed pr203/A1 — timeout: hidden all passed; affected failing none; 7 paths changed; no done-report; 2292.7 s, USD None
- fixed pr299/A1 — timeout: hidden all passed; affected failing none; 5 paths changed; no done-report; 1917.3 s, USD 1.1189412
- fixed pr125/D1 — verification: hidden {'tool/test_decision_flow.py::test_text_with_no_single_answer_that_ends_it_settles_nothing': 'failed'}; affected failing none; 5 paths changed; no done-report; 397.7 s, USD 1.1086758
- fixed pr191/D1 — verification: hidden {'tool/test_loop_progress.py::test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose': 'failed'}; affected failing none; 5 paths changed; claimed done; 1697.0 s, USD 0.9130868000000003
- fixed pr203/D1 — timeout: hidden all passed; affected failing none; 7 paths changed; no done-report; 2215.6 s, USD None
- fixed pr219/D1 — timeout: hidden all passed; affected failing none; 3 paths changed; no done-report; 1883.4 s, USD 1.3609538000000003
- pipeline pr125/A1 — spec: hidden all passed; affected failing none; None paths changed; no done-report; 0.0 s, USD 0.0; `Unterminated string starting at: line 12 column 5 (char 967)`
- pipeline pr191/A1 — verification: hidden {'tool/test_loop_progress.py::test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose': 'failed'}; affected failing none; 6 paths changed; claimed done; 1333.0 s, USD 0.9517673999999997
- pipeline pr87/A1 — verification: hidden {'tool/test_connect.py::test_connection_on_a_pc_with_only_one_cli': 'failed', 'tool/test_connect.py::test_connection_probe_does_not_start_an_uninstalled_cli': 'failed', 'tool/test_connect.py::test_connection_hub_respects_the_adapter_when_both_clis_are_installed': 'failed'}; affected failing {'tool/test_connect.py': ['test_the_move_writes_the_plan_it_checked_not_one_read_again']}; 7 paths changed; no done-report; 1322.1 s, USD 1.8927349999999996
- pipeline pr203/A1 — timeout: hidden all passed; affected failing none; 7 paths changed; no done-report; 2402.5 s, USD None
- pipeline pr28/A1 — timeout: hidden {'tool/test_agent.py::test_a_tool_line_shows_the_command_beside_what_it_is_for': 'failed', 'tool/test_main.py::test_a_cite_finds_its_file_the_ways_answers_write_it': 'failed'}; affected failing none; 11 paths changed; no done-report; 2064.9 s, USD 2.0811942
- pipeline pr125/D1 — spec: hidden all passed; affected failing none; None paths changed; no done-report; 0.0 s, USD 0.0; `Unterminated string starting at: line 12 column 5 (char 967)`
- pipeline pr191/D1 — verification: hidden {'tool/test_loop_progress.py::test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose': 'failed'}; affected failing none; 6 paths changed; claimed done; 1541.3 s, USD 1.0228544
- pipeline pr87/D1 — verification: hidden {'tool/test_connect.py::test_connection_on_a_pc_with_only_one_cli': 'failed', 'tool/test_connect.py::test_connection_probe_does_not_start_an_uninstalled_cli': 'failed', 'tool/test_connect.py::test_connection_hub_respects_the_adapter_when_both_clis_are_installed': 'failed'}; affected failing {'tool/test_connect.py': ['test_the_move_writes_the_plan_it_checked_not_one_read_again']}; 8 paths changed; no done-report; 1718.9 s, USD 2.2317886
- pipeline pr219/D1 — timeout: hidden all passed; affected failing none; 3 paths changed; no done-report; 1934.5 s, USD 0.9116342
- pipeline pr28/D1 — timeout: hidden {'tool/test_agent.py::test_a_tool_line_shows_the_command_beside_what_it_is_for': 'failed', 'tool/test_main.py::test_a_cite_finds_its_file_the_ways_answers_write_it': 'failed'}; affected failing none; 11 paths changed; no done-report; 2030.5 s, USD None

<!-- notes: kept across regenerations -->
### What the fixed-spec journal shows

- **One task carries the whole D−A difference.** Paired by task, A and D agree on nine of ten: both succeed on
  pr46, pr98 and pr166; both fail pr28, pr87, pr125, pr191, pr203 and pr299. The only split is pr219, where A
  succeeded in 1575 s and D's session was cut at the 1800 s limit (1883 s with verification) with the hidden test
  already passing. The −0.100 difference is that one cell; its interval [−0.300, 0.000] reflects how few tasks split.
- **Timeouts after the work was done.** Five of the twenty fixed cells timed out (A: pr203, pr299; D: pr203,
  pr219, pr299), and in all five every hidden test and every affected test passed on what the session had left in
  the workspace. The sessions were still working, none had written a done-report, when the harness stopped them;
  all three tasks carry the full gate (ruff, lint, debt and the whole `pytest tool -n 2` suite) as their first done
  item, which a session that runs it spends much of its 1800 s on. The primary metric, fixed before the run, counts a
  timeout as a failure, so they stay failures; read as "correct but unfinished", A would be 6/10 and D 6/10. This is
  a property of the 1800 s limit both conditions share, not a difference between them.
- **Failures are task-bound.** pr125 (A and D fail the same hidden test,
  `test_text_with_no_single_answer_that_ends_it_settles_nothing`), pr191 (both fail
  `test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose`, both claimed done) and pr87 (A misses
  one hidden test, D two) fail the same way under both inputs: the spec format does not change which condition of
  the request is missed.
- **Critical regressions.** pr28 broke existing `tool/test_main.py` tests under both conditions (A three, D two) and
  pr87 under A broke `test_the_move_writes_the_plan_it_checked_not_one_read_again`. None changed a forbidden path or
  deleted a tracked file, so the rule relative to A (D 1 against A 2) does not block D.
- **Production-runner baselines.** Condition A with the app's session flags succeeded on 3/10 (pr46, pr98, pr166)
  against the isolated A's 4/10; the difference is pr219, which timed out on the production runner too
  (2038 s). Three of the seven failures were timeouts.
- **Leakage limit.** Several cells changed exactly the files the merged pull request changed (pr203 and pr299 under
  both conditions, including `web/tests/task_browser.py` and `.wiki/ratchet.json`). The original text and the frozen
  specs name those files and the gate's `debt.py` step rewrites the ratchet, which accounts for it. The workspace is a
  history-free `git archive` of the merge base, but the session has Bash on the same disk as the hub checkout, and
  the journal keeps no tool transcript, so reading the hub's history cannot be excluded from the journal alone. This
  is the pilot's isolation, held identical as the spec requires; it affects A and D alike.

### What the full-pipeline journal shows

- **Two splits that cancel.** Paired by task, A and D agree on eight of ten: both succeed on pr46, pr98 and pr166;
  both fail pr28, pr87, pr125, pr191 and pr299. pr203 went to D (A timed out at 2402 s with its hidden test already
  passing) and pr219 to A (D timed out at 1934 s, hidden test passing) — the same timeout-after-the-work pattern as
  the fixed arm, landing once on each side. The 0.000 difference is those two cells; its interval [−0.300, +0.300]
  is as wide as ten tasks allow.
- **A generation that did not parse.** For pr125 the `next` generator ran 12 turns (USD 0.27) and returned a spec
  block whose JSON is cut off (`Unterminated string`), so neither condition had an input. That is a real failure of
  the full pipeline — production's first turn would refuse the same block — and is counted for both A and D; it is
  not an infrastructure failure (the model answered), so it was not rerun.
- **D pays for its extra spec calls.** The pipeline charges every repetition's spec calls to the conditions that
  need them: generation alone for A (USD 2.56 over the ten tasks), generation, enrichment and selection for D
  (USD 4.88). With the same four successes, that USD 2.33 of enrichment and selection is most of why D's cost per
  success is 1.23 times A's; the cells themselves cost A USD 13.28 and D USD 14.66.
- **Fresh specs satisfy fewer requirements.** Hidden-test pass fraction fell from 0.767 (A) and 0.733 (D) on the
  frozen specs to 0.500 for both on fresh ones: pr28 and pr299 timed out with hidden tests still failing under both
  conditions, and pr125 never ran. The drop is shared, so it does not separate A from D, but it is the variance the
  generation step adds and why the pipeline arm was run apart.
- **Critical regressions.** pr87 under both conditions broke `tool/test_connect.py` and failed all three hidden
  tests; no cell changed a forbidden path or deleted a tracked file, so D (1) against A (1) does not block.
- **No policy additions, no holds.** Neither D's ten frozen specs nor its nine fresh ones carry an assumed or
  undecided policy requirement, and none would have held production's first turn for a person.

### Reading the three judgments

- **Implementation pass: pass.** `tool/test_spec_views.py` passed at 58a359a, the only commit any counted cell ran
  on, at the start of both arms. (The report first read "not shown": pr125's two pipeline cells never started a
  session and carry no commit, which the check counted as one more commit. The report, not any measured row, was
  corrected after the run; the validity check already ignored commit-less rows.)
- **Experiment validity: valid.** The config committed at 58a359a was unchanged for every cell, the tasks meet
  neither the pilot's nor any tuned-on task, the frozen specs and hidden tests matched their digests, every cell ran
  with its cost recorded (a timeout the CLI reported no cost for counts its USD 3 cap: two cells and two
  baselines in the fixed arm, two cells in the pipeline), and main-1's cells are not pooled.
  The one limit is the leakage note above: the journal cannot show what a session read outside its workspace.
- **Adoption: further verification needed, so D is not adopted.** In neither arm does D clear a path: it is not
  better than A (quality path needs the difference's lower end above 0) and not cheaper per success (cost path needs
  a ratio at most 0.85; D is 1.21 and 1.23). Ten tasks leave intervals too wide to reject either path outright. No
  significant difference is not evidence that D and A are equivalent. A further test would need a far larger sample
  (the pilot's variance puts 80% power for +10 points at about 236 tasks) or a new experiment id with a session
  limit that stops cutting finished work — a change to the harness, so never a continuation of this one.
<!-- /notes -->

## Not run

- fixed: every cell ran.
- pipeline: every cell ran.
- A-B (the whole enrichment step), B-C (information equal) and C-D (execution equal): conditions B and C are not in main-2 by the owner's choice. main-1 ran 10 of its 80 cells (B 3, C 2) before it was stopped; those cells are in its journal and are not reported as a comparison. To run them, resume main-1 with `python tool/eval/spec_views.py run --config eval/spec-views/main-1.json` from a worktree at its code commit 88e0d13 with `raw/eval/spec-views/` copied in, after a new owner approval (its fixed ceiling has about USD 53 and 7.6 h left), then `pipeline` and `report` with the same config.
- Ceiling stops: none.

## The setting and its rollback

`SPEC_VIEWS` in the hub `.env` (read through `common.settings.pick`) stays `off`, its default, whatever this report says. It is turned on only when the adoption judgment above reads adopt and the owner turns it on; for candidate D that also needs the follow-up change named under the candidate, because the setting today renders the full agent view, not D. Rollback: set `SPEC_VIEWS=off` (or delete the line) in the hub `.env` and restart wiki-agent; sessions made after that get exactly main's input again (`tool/test_spec_views.py` holds the byte identity), and a session already running keeps its prompt until it is rebuilt.

