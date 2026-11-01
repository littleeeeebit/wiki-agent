---
scope: project
severity: preference
triggers: []
domain: ''
title: "Removal of ORCA dependencies and restoration of existing specification PR review loop"
pr: 67
merged: 2026-10-03
branch: "fix-native-hooks-review-recovery"
---

# Removal of ORCA dependencies and restoration of existing specification PR review loop

What. The task was canceled as the app internal implementation session selected an inherited ORCA skill and terminal transmission path. Manually posted PR #11 was not linked to the existing specification of the same branch, causing a `migrate-postgresql-pgvector-reduced-embeddings` duplication error that blocked the start of the review, and the agent's direct metadata modification was also rejected by the approval policy.

Why. Applied custom hooks and explicit execution boundaries to the app-owned session, and removed dependencies on existing ORCA terminal transmission, keepalive, and account directory auto-discovery. The app server links the PR to the existing specification of the same verified branch and starts the review. It preserves requirements, modification history, and implementation environments, and rejects checkouts that are in use or on different branches before changing metadata. Compatibility boundaries are applied to the `WIKI_AGENT_MANAGED=1` app-owned process. When editing this repository on a different host, it maintains the review cell and global hooks explicitly provided by that host. …

Source. PR #67 · `fix-native-hooks-review-recovery`
