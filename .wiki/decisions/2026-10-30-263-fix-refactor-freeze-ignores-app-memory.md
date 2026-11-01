---
scope: project
severity: preference
triggers: ["기억", "memory", "회상", "mem0", "저장소.{0,4}기억"]
domain: memory
title: "When the refactoring card commits all behavior-fixing tests, the app proceeds to the next step without being blocked by conversation memory or the 20-minute timer."
pr: 263
merged: 2026-10-30
branch: "fix/refactor-freeze-ignores-app-memory"
---

# When the refactoring card commits all behavior-fixing tests, the app proceeds to the next step without being blocked by conversation memory or the 20-minute timer.

What. This is the stage 1 problem of the ai-coding-wiki `preserve-repository-boundaries-3578a758` card (preserve mode). Stage 1 is the step of adding tests to fix the behavior before making changes. The agent committed all 26 test files, but the card failed to move to stage 2 (actual code cleanup). Therefore, the diff stopped at +2655/−0.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #263 · `fix/refactor-freeze-ignores-app-memory`
