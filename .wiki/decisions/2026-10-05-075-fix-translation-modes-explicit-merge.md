---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refinement of translation modes and modification of review merge permissions and default branch synchronization"
pr: 75
merged: 2026-10-05
branch: "fix/translation-modes-explicit-merge"
---

# Refinement of translation modes and modification of review merge permissions and default branch synchronization

What. Translation settings are categorized into off, fully enabled, and partially enabled. - In partially enabled mode, only the last completed response, questions, and options are translated, while wiki, retrospective, and next-task conversations are displayed entirely in Korean. - Automatic merging is removed so that review approval alone does not trigger a merge, and user clicks are bound to prepared commits and specification versions. - Upon clicking merge, wiki linting is corrected, documents are indexed, existing `.omm` are committed and pushed to the same PR, and the changed commits are reviewed again. …

Why. Saved automatic merge settings and default values used review approval as merge permission. It was possible to start the next task even before the cleanup of a busy checkout was finished, and there was a path where only the auxiliary default branch was updated. `.omm` generation was also executed after the merge and was not included in the PR. Detailed analysis and scope are recorded in `docs/research/merge-authorization-maintenance.md`. - Full backend execution: 1,530 passed, 2 skipped, 12 initial failures. All 12 of those failures passed in subsequent runs targeting the modified code and tests. - Translation mode API, actual Git-based merge, maintenance, and synchronization regression verification passed. …

Source. PR #75 · `fix/translation-modes-explicit-merge`
