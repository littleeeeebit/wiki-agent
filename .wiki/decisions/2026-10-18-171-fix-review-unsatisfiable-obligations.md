---
scope: project
severity: preference
triggers: []
domain: ''
title: "review: prevent general tasks from stalling due to unfulfillable review conditions"
pr: 171
merged: 2026-10-18
branch: "fix/review-unsatisfiable-obligations"
---

# review: prevent general tasks from stalling due to unfulfillable review conditions

What. If the task specification, rather than the refactoring execution, contains `refactor` or `differential`, it is ignored in the review contract. This check can only run if there is a fixed specification for the refactoring execution, so it was forever trapped in `멈춤 — 로컬 검증 준비` in general tasks. - `performance` no longer stops unconditionally. The reviewer judges based on the measurements in the PR according to the standard phrase ("requires before and after measurements"). "There are no measurements taken here" remains as is in the review instructions. …

Why. rfg_rag #64 and #66 stalled at the same spot. This is because the planning guide stated that these items could also be selected for general tasks.

Source. PR #171 · `fix/review-unsatisfiable-obligations`
