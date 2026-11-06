---
scope: project
severity: preference
triggers: []
domain: ''
title: "Proceed with the implementation of the independent preliminary review before the completion report."
pr: 211
merged: 2026-10-22
branch: "fix/prerequisite-review-handoff"
---

# Proceed with the implementation of the independent preliminary review before the completion report.

What. Execute the independent preliminary review as the first step of the same review loop through the optional `prerequisite_review` declaration of the specification. Once a user starts a review loop, it proceeds in the order of preliminary judgment, implementation resumption, full completion report, general approval review, and final verification. - The app preserves the actual identifier of the read-only reviewer, the input file SHA-256, the reviewed commit, and the judgment JSON. The implementation session must commit that judgment as is and complete all completion conditions of the existing specification. …

Why. For the English question bank task, the independent equivalence review must pass first before production data can be applied, and full completion requires actual application, deployment, search, and browser verification. The existing app could accept manually posted PRs before the completion report, but because there was no step to return the preliminary judgment to the implementation, the general completion review and production environment application conditions conflicted. Even after the modification, the full completion conditions and final verification both remain.

Source. PR #211 · `fix/prerequisite-review-handoff`
