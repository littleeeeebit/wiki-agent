---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Summary of 3 gate instability tests and correction of shadow timeout classification"
pr: 99
merged: 2026-10-09
branch: "fix/gate-flakes"
---

# Summary of 3 gate instability tests and correction of shadow timeout classification

What. I ran the entire gate for #98 three times, and one different test failed each time (8 workers). All three cases were code unrelated to #98.

Why. 1. `test_review_contract::test_slow_manifest_git_is_reaped...` - Reproduced 2 out of 16 times under load. - Actual defect: When `shadow_context`'s `git diff` hits the shared timeout, `TimeoutExpired` is uploaded as is and recorded as `unavailable`. I fixed it to call `budget.check()` first to be classified as `exhausted`, in the same way as `manifest()`. - Parameterized the test into two cases: `ls-files`/`diff`. The `diff` case fails without this fix. - Relaxed the 2-second delay limit to 30 seconds. …

Source. PR #99 · `fix/gate-flakes`
