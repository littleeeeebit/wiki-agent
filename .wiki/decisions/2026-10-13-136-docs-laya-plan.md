---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya plan: Replace Jev with 14 fine-tuned models by type"
pr: 136
merged: 2026-10-13
branch: "docs/laya-plan"
---

# Laya plan: Replace Jev with 14 fine-tuned models by type

What. This is a plan to replace Jev (hosting `jev-1.13.0`) with Laya models fine-tuned by the decisions in this repository. We will train 14 models in total, one for each type of judgment, and the code will select which model to use based on the judgment type. This PR only changes the documentation; there are no code changes or paid calls.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #136 · `docs/laya-plan`
