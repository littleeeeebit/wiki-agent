---
scope: project
severity: preference
triggers: []
domain: ''
title: "For each refactoring specification, you can reselect the implementation/review model, and a new investigation clears previous unstarted specifications."
pr: 236
merged: 2026-10-25
branch: "refactor-card-roles"
---

# For each refactoring specification, you can reselect the implementation/review model, and a new investigation clears previous unstarted specifications.

What. For each specification [Change Model]: You can reselect the implementation/review model for investigation specifications that have not yet started (organized). - The investigation model remains as is. [Re-verify] and conversations continue to run on the investigation model. - Specifications that have started continue with the same model until the end. - If the implementation and review models are the same, it only warns but does not block. - The change history is recorded in `refactor.role_changes`. - Server path: `POST /api/refactors/specs/{sid}/roles`. …

Why. Even if the quota became insufficient after finishing up to C among specifications A~Z from the investigation, there was no way to change the implementation model for the remaining specifications. This is because the model selected when starting the investigation is fixed for all specifications (#232).

Source. PR #236 · `refactor-card-roles`
