---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "feat: Bind the final gate to the allowed head and reduce test burden (Reliability Phase 2)"
pr: 47
merged: 2026-09-29
branch: "feat/reliability-2-tests"
---

# feat: Bind the final gate to the allowed head and reduce test burden (Reliability Phase 2)

What. The review loop ran the entire `gate_cmd` (7-9 minutes here) every time a fix was made, and there was no guarantee that the commit allowed by the review and the commit that passed the tests were the same. The test suite had accumulated stale tests, smoke tests, and asserts that could not fail, and there were three script-only suites that the gate never ran. Reliability Plan Phase 2 (`docs/plans/reliability/2-tests.md`).

Why. Gate contract - Repair rounds only run checks that are triggered by `[checks] paths` in the changed path. If it is a file that is not triggered, shared configuration, incorrect `paths`, or no merge base, it expands to the full gate - Run the full gate once on the exact commit allowed by the review, and `[머지]` is bound to that head, merge base, and environment digest. New commits require new results - Results are in `validation` and the only place that writes to them is `specs.validate`. The review tab shows partial checks and the final gate separately - I wrote down in `docs/plans/loop/4-review.md` what was intentionally changed from the original loop's "full gate per round" ...

Source. PR #47 · `feat/reliability-2-tests`
