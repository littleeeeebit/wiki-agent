---
scope: project
severity: preference
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Implementation of Reliability PR 9: paired Korean experiments, fact unit tests, fixed cohort gate"
pr: 134
merged: 2026-10-13
branch: "feat/reliability-9-pairs"
---

# Implementation of Reliability PR 9: paired Korean experiments, fact unit tests, fixed cohort gate

What. This is the implementation of Reliability PR 9. It corresponds to step 2 of the design (v1) merged in #133. Only fake hosts are used, and there is no paid execution. The pair set (`eval/jev/korean/pairs.json`) and Korean twins will be created in the next PR.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #134 · `feat/reliability-9-pairs`
