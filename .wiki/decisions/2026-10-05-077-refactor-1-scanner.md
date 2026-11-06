---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Refactoring 1/6: Add debt scanner and ratchet to final gate"
pr: 77
merged: 2026-10-05
branch: "refactor/1-scanner"
---

# Refactoring 1/6: Add debt scanner and ratchet to final gate

What. This is step 1 of the refactoring feature series (6 stack PRs). It re-evaluates the debt accumulated through vibe coding and prevents it from accumulating again.

Why. `tool/debt.py`: Scans the repository with language-agnostic metrics (lines of code, 6-line window duplication, longest block, 180-day change count). There is a `scan|init|check|tighten` command. - Ratchet `.wiki/ratchet.json`: Sets a baseline per file and an upper limit for new files (800 lines, 1500 lines for tests). To raise the standard, there must be a new `reason` in the same change. - The final gate (`specs.required`) ends with `debt.py check` in all repositories. Repositories without a ratchet file pass. - Since merge preparation (`maintenance.prepare`) only lowers the standard with `tighten`, the amount reduced by this PR remains reduced. …

Source. PR #77 · `refactor/1-scanner`
