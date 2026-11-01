---
scope: project
severity: preference
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Prevents the gate where artifacts of a suspended merge preparation are re-run from stopping due to \"uncommitted changes\"."
pr: 226
merged: 2026-10-23
branch: "fix/gate-sets-aside-interrupted-preparation"
---

# Prevents the gate where artifacts of a suspended merge preparation are re-run from stopping due to "uncommitted changes".

What. ai-companion-voice-agent `plan-32916e65` has stopped with "Stopped — gate / gate failed twice in a row — uncommitted changes".

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #226 · `fix/gate-sets-aside-interrupted-preparation`
