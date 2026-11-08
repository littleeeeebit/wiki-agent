# Spec views — pilot-1: pilot, not the main experiment

This is a pilot. It checks the harness, the B–C equivalence, the journal and the cost accounting. Its sample is far too small to support any adoption claim, and nothing here judges adoption.

Implementer: `claude-opus-5-5` at effort `high`, 2 repetitions, concurrency 3, ceilings USD 40 and 8 h. Config digest `3536e32ee2da`. Spent USD 37.9952 and 4.535 h, preparation included.

## Verified task success per condition

| Condition | Cells run | Successes | Success rate (95% CI over tasks) | Cell USD | First-run spec USD | USD per verified success | Regressions | Critical | Forbidden | Failures |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A | 11 | 6 | 0.500 [0.167, 0.833] | 8.5912 | 1.2077 | 1.6331 | 0 | 0 | 0 | timeout 3, verification 1, error 1 |
| B | 11 | 5 | 0.417 [0.083, 0.833] | 11.192 | 2.4505 | 2.7285 | 2 | 2 | 0 | verification 2, timeout 4 |
| C | 10 | 5 | 0.417 [0.083, 0.833] | 8.301 | 2.4505 | 2.1503 | 2 | 2 | 0 | timeout 2, verification 3 |
| D | 11 | 6 | 0.500 [0.167, 0.833] | 7.2546 | 2.651 | 1.6509 | 1 | 1 | 0 | verification 3, timeout 2 |

Cost per verified success divides every run's cost, failed runs included, plus the condition's first-run spec-generation cost, by its verified successes.

## Paired differences

Per task, the mean success over repetitions of one condition minus the other; the interval resamples tasks with their repetitions together (95% percentile bootstrap).

| Difference | Value [95% CI] |
| --- | --- |
| A-B | +0.083 [-0.250, +0.500] (n=6) |
| B-C | +0.000 [+0.000, +0.000] (n=6) |
| C-D | -0.083 [-0.250, +0.000] (n=6) |
| D-A | +0.000 [-0.417, +0.333] (n=6) |

## Spec-generation cost

Each condition as if it ran alone: A needs the generator's spec, B and C its enrichment too, D also its selection. Repetitions reuse the frozen spec (cached, USD 0.0). Actually spent once for all: USD 2.6511.

| Condition | First-run USD over all tasks |
| --- | ---: |
| A | 1.2077 |
| B | 2.4505 |
| C | 2.4505 |
| D | 2.651 |

## Production-runner baseline

The controlled runner differs from production: a fresh one-commit repository from git archive instead of a worktree (no history, no remote, no PR or review loop); one isolated Claude Code session per cell (--system-prompt-file replacing the host prompt, no user or project settings, hooks, CLAUDE.md, MCP servers or AskUserQuestion, nothing persisted) instead of the app's write session with hooks, Jev's first-turn file filter and a person to ask; the first turn is only `Start.`; verification runs the hidden and affected tests rather than the adapter gate. The production-runner baseline runs condition A once per task with the app's session flags (host prompt kept, the person's settings and hooks loaded) and adds the merge base's Python gate command; the npm and browser steps are left out because the archived workspace has no node_modules.

| Task | Result |
| --- | --- |
| pr304 | not run — the usd ceiling stopped the run first |
| pr198 | not run — the usd ceiling stopped the run first |
| pr260 | not run — the usd ceiling stopped the run first |
| pr164 | not run — the usd ceiling stopped the run first |
| pr106 | not run — the usd ceiling stopped the run first |
| pr161 | not run — the usd ceiling stopped the run first |

## Cells

Ran (43): pr106/A1, pr106/A2, pr106/B1, pr106/B2, pr106/C1, pr106/C2, pr106/D1, pr106/D2, pr161/A1, pr161/A2, pr161/B1, pr161/B2, pr161/C1, pr161/C2, pr161/D1, pr161/D2, pr164/A2, pr164/B1, pr164/B2, pr164/C2, pr164/D1, pr164/D2, pr198/A1, pr198/A2, pr198/B1, pr198/B2, pr198/C1, pr198/C2, pr198/D2, pr260/A1, pr260/A2, pr260/B1, pr260/C2, pr260/D1, pr260/D2, pr304/A1, pr304/A2, pr304/B1, pr304/B2, pr304/C1, pr304/C2, pr304/D1, pr304/D2.

Not run (5): pr164/A1, pr164/C1, pr198/D1, pr260/B2, pr260/C1.

The usd ceiling stopped the run at USD 37.9952 and 4.063 h.

4 cells ended without a reported cost (a timeout or a crash cut the session before its result) and were charged at their cap, USD 12.0 in total: pr161/B1, pr161/C1, pr198/B2, pr304/A1. The ceiling counts these bounds, so the spend actually billed is lower than the spend shown.

Resume: the command below runs the cells not yet run while the ceilings have room; beyond them a new experiment id with its own committed config is required.

```bash
python tool/eval/spec_views.py run --config eval/spec-views/pilot-1.json
```

Rows from a scripted stub implementer are never counted here (0 excluded).

Code commits the counted cells ran on: `36123b312fa7` (4), `187bd136d63c` (39).

## Equivalence

| Task | B–C equivalent |
| --- | --- |
| pr304 | True |
| pr198 | True |
| pr260 | True |
| pr164 | True |
| pr106 | True |
| pr161 | True |

## Adjustments during the pilot

- narrowed pr198's affected tool/test_loop.py from `-k merge` (34 tests, 16 minutes per evaluation) to the one existing merge test the change runs through, so evaluation fits the session and wall budgets — tuned on pr198; the main experiment must exclude these tasks.
- harness repairs after the run: frozen records must be the ones committed with the config, hash to their digest, carry the configured selection, a discriminating reference and B–C equivalence; a hidden test passes only when every case passed and its pytest exited 0; forbidden paths are found behind ignore rules; verification and the production gate stop at the shared wall deadline; preparation spends and counts time inside the ceilings; cost per success keeps every attempt. The 43 cells journalled before were judged by the earlier rules: none ran past the wall deadline (the USD ceiling stopped the run at 4.06 h), every pr164 cell failed, no infrastructure rerun occurred and no preparation step was repeated, so none of those changes moves a reported number; a hidden pytest exit code was not journalled then, so a passing hidden test with a non-zero exit cannot be ruled out for those rows — not tuned on any task.
- harness repairs after the run: a reference separates the answer only when a hidden test actually failed on the merge base (a skip, a missing test or an error no longer counts), recomputed at admission; pytest and the production gate run in a job whose whole process tree is killed at the shared deadline. Every pilot task's reference had a hidden test failing on its merge base, and no cell reached the wall deadline, so no reported number moves — not tuned on any task.
