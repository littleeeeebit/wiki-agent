---
scope: project
severity: preference
triggers: []
domain: ''
title: "hub·connection repository self-improvement pipeline separation"
pr: 68
merged: 2026-10-03
branch: "feat-separated-self-improvement"
---

# hub·connection repository self-improvement pipeline separation

What. Separated the self-improvement executor for the wiki-agent itself (`hub`) and each connected repository (`project`). Isolated history, evidence, budget, evaluation contract, cache, and adoption status using Git common directory identifiers. - Project private results are not automatically promoted to the public wiki. General conversations and Stop hooks do not initiate experiments. …

Why. Initial implementation `da89020`: Total tests 1,406 passed, 1 skipped, exit 0. - Guard fix `9f045cc`: Evaluator tests 8 passed. - Total gate `python -m pytest tool --tb=short` before merge of approved `873c23d`: 1,424 passed, 1 skipped, 1 existing TestClient deprecation warning, 1,355.92 seconds, exit code 0. - This hard limit·native rejection fix: Self-improvement executor·evaluator·search boundary tests 52 passed, 133.79 seconds. This is not the result of re-running total tests for this fix. - `python -m ruff check tool`, `python tool/lint. …

Source. PR #68 · `feat-separated-self-improvement`
