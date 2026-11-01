---
scope: project
severity: preference
triggers: []
domain: ''
title: "Do not provide host fallback for judge requests of analysis questions (L1)"
pr: 118
merged: 2026-10-11
branch: "fix/analysis-coverage-no-host"
---

# Do not provide host fallback for judge requests of analysis questions (L1)

What. Implemented L1 recommended in the Analysis path section of `answer-path.md`. Questions determined as analysis by the route do not receive host fallback in judge requests.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #118 · `fix/analysis-coverage-no-host`
