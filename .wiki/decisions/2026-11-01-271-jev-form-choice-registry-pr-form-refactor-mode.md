---
scope: project
severity: preference
triggers: []
domain: ''
title: "Let Jev choose the format of the PR post and the mode of the refactoring specification, but for now, only record it."
pr: 271
merged: 2026-11-01
branch: "jev-form-choice-registry-pr-form-refactor-mode"
---

# Let Jev choose the format of the PR post and the mode of the refactoring specification, but for now, only record it.

What. Added a new type to the judgment list for points where one must choose from a few set options. Each point includes the options, a one-line description, the default answer determined by the code, and a fixed set of examples for grading. Two points appear as 'not yet verified' in the judgment list on the status screen. - Location: `FORMS`, `choose_form` - Divided the PR post request form into a set of common rules and guidance by format (feature addition, bug fix, cleanup without behavioral changes, planning document, file-only changes). …

Why. When the task is finished, the server requests the worker to write a PR post for humans to read. The refactoring investigation proposes things to fix as specifications, and each specification proceeds in one of three modes: splitting, removing clutter, or fixing the fundamental structure, while keeping the behavior the same. Jev is a judgment model that chooses one of the options provided by the server. Sending the same request form for all PRs resulted in posts written like feature additions even for bug fixes or planning documents. Instead of attaching separate code for each choice point, they were bundled into a single framework. Since there was a tendency for judgments to lean toward the first option, a match rate with the order reversed was also included in the passing conditions.

Source. PR #271 · `jev-form-choice-registry-pr-form-refactor-mode`
