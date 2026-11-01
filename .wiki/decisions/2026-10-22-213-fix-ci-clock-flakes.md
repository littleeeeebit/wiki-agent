---
scope: project
severity: preference
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Fix two clock-bound CI flakes in test_korean and test_loop"
pr: 213
merged: 2026-10-22
branch: "fix-ci-clock-flakes"
---

# Fix two clock-bound CI flakes in test_korean and test_loop

What. In this session, the CI for PR #207 and #210 failed once each due to tests unrelated to the changes. Both passed when re-run, and they also pass locally. The cause for both is a timeout that is too tight given the CI load.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #213 · `fix-ci-clock-flakes`
