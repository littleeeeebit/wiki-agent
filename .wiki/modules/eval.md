---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# eval/ — Jev and Laya evaluation fixtures, and the fitted policies production reads

## What it is for

Tracked inputs for evaluating the decision models: Jev (`eval/jev/`) and
Laya (`eval/laya/`). There is no code here. The scripts that read and write
these files are `tool/eval/*.py` and `laya/*.py`. Most files are labelled
datasets or manifests. A few are fitted artifacts that runtime code reads.

## Contents, by who reads them

- Smoke and retrieval manifests: `eval/jev/smoke.json` (queries plus corpus;
  `tool/eval/actions.py:55`, `tool/eval/answers.py:42`,
  `tool/eval/decisions.py:40`) and `eval/jev/bridge.json`
  (`tool/eval/retrieval.py:35`).
- Intent and gate sets for the stage-10 comparison: `eval/jev/intents.json`,
  `eval/jev/gates.json` (`tool/eval/dataset.py:40`,
  `tool/eval/report.py:37`). There are versioned copies under
  `eval/jev/reliability*/`. The newest, `reliability-v8/intents.json`, is
  the one `tool/eval/arms.py:69` uses.
- Calibration data and the fitted policy: `eval/jev/calibration.json`,
  `calibration.scores.json` and the output `eval/jev/policy.json`
  (`tool/eval/policy.py:67-69`). The same for claims:
  `relation.json`, `relation.scores.json` and `relation-policy.json`
  (`tool/eval/policy.py:247-248`, `tool/decision/claims.py:8`), plus
  `answers.json` and `faithful.json` (`tool/eval/policy.py:256-257`).
- Other checks: `extraction.json` (`tool/eval/graph.py:3`), `meaning.json`
  (`tool/eval/meaning.py:28`), `actions.json` (`tool/eval/compare.py:86`),
  `branches/<point>.json` (`tool/eval/branches.py:4`), and
  `review-routing*.json` (`tool/eval/review_routing.py:27`,
  `tool/test_review_routing.py:25`).
- Laya: `eval/laya/tests.json` is the reserved gate test (`laya/gate.py:25`;
  header `"reserved": "2026-10-13"`). `eval/laya/policy/<kind>.json` holds
  per-kind policies fitted on a Laya checkpoint and written by
  `tool/eval/policy.py:103,483`.

## Depends on, depended on by

- The runtime depends on two files here. `decision.policy` loads
  `settings.HUB / eval/jev/policy.json` (`tool/decision/policy.py:26,95`),
  and `decision.claims` names `eval/jev/relation-policy.json`
  (`tool/decision/claims.py:8`). Without the file every kind stays
  provisional, at 0.2/0.8 (`tool/decision/policy.py:8-11,97-99`).
- `laya/jev_gate.py:41` reads `eval/jev/policy.json` to score Jev the way
  production would.
- Nothing under `tool/decision/` reads `eval/laya/policy/` yet.

## What trips a newcomer

- `policy.json` is bound to a model and a prompt digest. If the model or
  prompt version differs, every kind falls back to provisional, and the only
  sign is a `problem` field (`tool/decision/policy.py:102-109`). Changing a
  prompt in `tool/main/knowledge.py` can silently unfit the policy; the
  comment at `tool/main/knowledge.py:194` keeps one prompt out of `PROMPTS`
  for exactly this reason.
- These are hub files. Connected repositories read the hub's policy through
  `settings.HUB` (`tool/decision/policy.py:95`), not a copy of their own.
- Run output does not belong here. Scripts write to `raw/eval/jev/`
  (`tool/eval/baseline.py:42`, `tool/eval/meaning.py:29`) or to an ignored
  `eval/**/results/` folder (`.gitignore:34-35`, `tool/eval/file_filter.py:38`).
  The exception is `eval/jev/review-routing-results.json`, which is tracked
  on purpose as versioned evidence (`docs/plans/review-routing/2-jev-shadow.md:153-154`).
- The `reliability`, `reliability-v2` … `reliability-v8` folders are frozen
  history. Do not edit an old version; add a new one.
- `eval/laya/tests.json` must never feed training or augmentation; the loop
  keeps it out (`laya/README.md:62`). Regenerating it invalidates every gate
  verdict read so far.
- Several runs here are paid model calls: TypeSafe for Jev, Gemini for
  Korean (`tool/eval/graph.py:13`). Read a script's docstring before running
  it.
