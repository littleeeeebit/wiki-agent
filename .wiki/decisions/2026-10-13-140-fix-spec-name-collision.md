---
scope: project
severity: preference
triggers: []
domain: ''
title: "Review loop: Allows selecting pending PRs for work, and does not open two PRs for the same branch."
pr: 140
merged: 2026-10-13
branch: "fix/spec-name-collision"
---

# Review loop: Allows selecting pending PRs for work, and does not open two PRs for the same branch.

What. In rfg_rag, PR #19 was blocked from selecting a review loop, and starting #18 resulted in a "specification `preprocessing-v11-input` with the same name already exists" error. Two causes were fixed.

Why. After #72, PRs issued by the app wait for a human "review loop" request in the `PR #n` state, but `refusal()` in the PR list was blocking this state as "already PR #n". Therefore, there was no way to start the loop if there was no fault. The agent in the work turn opened #18 directly to `dev` with the `preprocessing-v11-input` branch. Since `existing_pr()` in the issuance stage only looked for PRs with base `main`, it created another #19 in `main` with the same branch. Because the specification name comes from the branch, #18 collided with the name of #19's specification.

Source. PR #140 · `fix/spec-name-collision`
