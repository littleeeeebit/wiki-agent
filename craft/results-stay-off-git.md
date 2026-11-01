---
scope: craft
severity: contract
repeat: rule
triggers: ["벤치마크|benchmark", "(평가|eval|측정|리플레이|replay)\\s*(결과|result)", "결과\\s*(json|JSON|파일)", "(json|JSON)\\s*(결과|파일)(을|를|이|가)?\\s*(커밋|올리|넣|빼)", "eval/"]
slots: []
sources: []
sources_withheld: true
links: [verify-narrow-then-wide, measure-after-the-last-change]
---

# What a run writes stays on the machine — git keeps the script, not its output

Rule. An evaluation, benchmark or replay writes its output under a `results/`
folder (`eval/**/results/`, ignored by git) and never commits it. What the
pull request carries is the script, the fixtures it reads, and the numbers that
matter written into the PR body or the plan, with the command that produced
them. A new evaluation script names its output path under `results/` from its
first commit. A fixture, gate or corpus a test reads is input, not output: it
is named for what it holds and stays tracked. If a run's output is already
tracked, `git rm --cached` it in the same change that moves the path.

Why. The person said to keep result JSON out of the diff. It was applied to one
folder (`eval/jev/branches/results/`, #271) instead of as a rule, and the same
day `tool/eval/file_filter.py` committed `eval/jev/file-filter/2026-11-01.json`
— 64 KB of per-spec replay rows — in #270. A reviewer reads every line of a
diff it is given; long generated JSON buries the code it is meant to judge, and
it is rewritten on every rerun, so each run is another diff.

What goes wrong. The review spends its attention on rows nobody wrote, and the
result file conflicts or churns on every run. A number kept only in a JSON
nobody opens is not evidence either — the figure that decides something
belongs in the prose that decides it.

The check. `lint` reports any tracked file under `eval/` that sits in a
`results/` folder or is a JSON named by a date or UTC stamp.
