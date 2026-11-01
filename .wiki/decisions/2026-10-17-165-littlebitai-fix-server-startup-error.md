---
scope: project
severity: preference
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Unifying verification responsibility: CI and restricted local execution, preventing redundant checks"
pr: 165
merged: 2026-10-17
branch: "LittleBitAI/fix-server-startup-error"
---

# Unifying verification responsibility: CI and restricted local execution, preventing redundant checks

What. Development and review repairs only execute impact scope checks. If the mapping is unknown, only patch checks are performed, and broad verification is passed to the final person in charge. Additional checks that have already passed are not repeated. - Public/active workflows select hosted CI, while private/CI-unconfigured/explicit local settings select local responsibility. After selecting responsibility, a full local check is not started due to CI failure or communication errors. - Reuse evidence of passing for the same commit, merge base, command, configuration, and environment. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #165 · `LittleBitAI/fix-server-startup-error`
