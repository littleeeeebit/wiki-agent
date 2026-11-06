---
scope: project
severity: preference
triggers: []
domain: ''
title: "Added update notification card and per-task test organization steps"
pr: 100
merged: 2026-10-09
branch: "feat/update-notice-test-cleanup"
---

# Added update notification card and per-task test organization steps

What. Update notification (Orca method): The server fetches `origin/main` at most every hour and compares it with the commit at the time the app was opened. If it is behind, it shows a list of new commits in a collapsible card at the bottom of the rail. No dialog, silently skips on failure, "Later" hides only that version, no automatic restart. - "Update": Only `git pull --ff-only` a clean `main` checkout. If changed, `pip install -r requirements-chat. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #100 · `feat/update-notice-test-cleanup`
