---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring 2/6: Add refactoring profile to improvement runner"
pr: 78
merged: 2026-10-05
branch: "refactor/2-runner"
---

# Refactoring 2/6: Add refactoring profile to improvement runner

What. This is step 2 of the refactoring series. We add a refactoring profile to the improvement runner (`tool/improvement.py`). This will be reviewed in conjunction with step 3.

Why. `tool/refactor_profile.py`: Freeze one step as an experiment. Property tests are protected, candidates must observe tests and ratchets (`preserve`), and the candidate with the greatest debt reduction wins (`shrink`, scoring type). Holdout is the repository-wide gate. If all candidates fail, retry once and then return a request to split the step. - The proposer is a native session within the candidate checkout (`improvement_host.propose`). Only the refactoring profile can exceed the proposal limit by `soft_caps: ["propose"]`, and the excess amount remains in `overruns`. …

Source. PR #78 · `refactor/2-runner`
