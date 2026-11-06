---
scope: project
severity: preference
triggers: []
domain: ''
title: "Added per-file diff, FAST mode, and automatic .omm structure view"
pr: 69
merged: 2026-10-04
branch: "feat/task-diff-fast-architecture"
---

# Added per-file diff, FAST mode, and automatic .omm structure view

What. Added a full list of modified files and expandable per-file diffs to the code change status. Claude/Codex's built-in FAST mode can be toggled on and off, with the default set to OFF. In the app structure screen in the center, you can view local `.omm` documents and Mermaid diagrams. The structure document is automatically updated every 5 seconds while the server is running and before the frontend build, while preserving maintenance notes.

Why. Verification: Passed relevant backend tests, desktop/mobile Playwright checks, frontend build/lint, Ruff, wiki lint, and whitespace and UTF-8 BOM checks. Confirmed FAST OFF execution and actual hook behavior in new read-only sessions for Claude and Codex. Verified FAST settings and request forwarding, but did not measure processing speed or usage eligibility for paid accounts. Windows symbolic link checks require creation permissions not present in this environment. Removed inherited Gemini credentials at pytest startup and added new process regression checks. Ensures integration tests do not send actual requests using the machine's translation keys. Maintained the product's credential handling and existing test wait limits. `gpt-5. …

Source. PR #69 · `feat/task-diff-fast-architecture`
