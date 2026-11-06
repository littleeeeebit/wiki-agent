---
scope: project
severity: preference
triggers: []
domain: ''
title: "fix: prevent children of the separated search daemon from opening windows"
pr: 46
merged: 2026-09-28
branch: "reliability-1-processes"
---

# fix: prevent children of the separated search daemon from opening windows

What. Reliability plan phase 1(`docs/plans/reliability/1-processes.md`). Fix the issue where the console window flashes when asking a question in the desktop app. Cause (confirmed via process capture): `search.spawn` launches the search daemon as `DETACHED_PROCESS`, so the daemon has no console. The `git ls-files` called by the daemon while indexing (`tool/search/sources. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #46 · `reliability-1-processes`
