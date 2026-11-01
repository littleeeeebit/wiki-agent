---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix background progress, architecture refresh, and review authorization"
pr: 72
merged: 2026-10-04
branch: "fix/background-progress-architecture-review-pause"
---

# Fix background progress, architecture refresh, and review authorization

What. Displays Claude background task lifecycle, tool results, Codex commands, and MCP progress in the agent window. - Keeps the session active through background completion and Claude's automatic follow-up instead of terminating at the first foreground result. - Replaces import-only architecture generation with native CLI model analysis of execution paths, state, and data ownership. `.omm` is regenerated only after merged PR cleanup, and general polling reads saved documents. …

Why. Relevant backend regression groups including protocol events, raw output playback, publishing, planning, merge cleanup, and architecture generation have passed. - Actual Claude background commands completed via terminal notifications and automatic follow-up. - Progress, completion, idle UI, raw output translation exclusion, and all saved architecture diagrams passed in desktop/mobile browser inspection. - Frontend build, Python Ruff, wiki lint, whitespace, and BOM-free UTF-8 checks passed. Existing frontend warnings are maintained, and one Windows symbolic link check was skipped. - New native Claude and Codex hook reception confirmations are recorded locally before review dispatch. …

Source. PR #72 · `fix/background-progress-architecture-review-pause`
