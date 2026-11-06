---
scope: project
severity: preference
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Full refactoring directory scope, stop after specification change, gate display, fix model selection by task"
pr: 194
merged: 2026-10-20
branch: "LittleBitAI/fix-full-refactor-scope"
---

# Full refactoring directory scope, stop after specification change, gate display, fix model selection by task

What. 1. Full refactoring `spec` block rejection — In ai-companion-voice-agent full mode, the agent put package directory `src/ai_companion_voice_agent` into `refactor.files`, and the server check rejected it as "not a file in the repository" because it only accepts regular files. The prompt called `files` the "initial exploration target," but the server only accepting files caused a mismatch. 2. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #194 · `LittleBitAI/fix-full-refactor-scope`
