---
scope: project
severity: preference
triggers: []
domain: ''
title: "Stop orphaning background work and blocking review on it"
pr: 86
merged: 2026-10-06
branch: "fix/background-task-orphans"
---

# Stop orphaning background work and blocking review on it

What. `ChatSession._drain` keeps a work turn open after the answer while Claude background tasks run.

Why. 1. Background session ended at 10 minutes, orphaning its work. The wait still had the 600s silence clock. A quiet test suite or model sweep tripped it: the CLI was killed, the turn failed with `no answer within 600 seconds. …

Source. PR #86 · `fix/background-task-orphans`
