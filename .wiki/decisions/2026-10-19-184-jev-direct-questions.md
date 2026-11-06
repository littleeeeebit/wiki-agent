---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Ask Jev with B-branch questions — Step 11 Step 3: Production reflection, re-fitting, held-out gate"
pr: 184
merged: 2026-10-19
branch: "jev-direct-questions"
---

# Ask Jev with B-branch questions — Step 11 Step 3: Production reflection, re-fitting, held-out gate

What. `tool/main/knowledge.py` - Changed `PROMPTS` to B phrasing. coverage cites the original requirement, and source asks for specific conditions. - conflict was split into two questions `conflictquery` and `conflictother`. Combination is done by `conflicted`: P(query)·(1−P(other)). - `judge_questions` receives the requirement text. - `Flow. …

Why. In Step 11 measurement (#182), only the B(direct) branch was adopted. The policy cost of the calibration split decreased from 83.5 to 44.5, and there were no increased kinds. A, C, and D are rejected. The Step 3 plan (`docs/plans/jev/11-question-shape.md`, owner approval 2026-10-19) is to put B's questions into production, re-fit the policy, and run the held-out gate once.

Source. PR #184 · `jev-direct-questions`
