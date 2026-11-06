---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: agent only performs approved writes in the worktree, and workspace creates and cleans up the worktree"
pr: 6
merged: 2026-09-24
branch: "wiki-agent-5-agent-workspace"
---

# feat: agent only performs approved writes in the worktree, and workspace creates and cleans up the worktree

What. Step 5 — `docs/plans/wiki-agent-5-agent-workspace.md`.

Why. `ChatSession(write=True)` — Opens only in the worktree. All writes come as `approval` events and respond with `answer(id, allow)` - Claude: `--permission-prompt-tool stdio`'s `control_request` - Codex: `app-server` (`read-only` + `untrusted`)'s `requestApproval`. Read sessions remain as `exec` - Writes outside the worktree are rejected without asking. Turn time does not pass while waiting for approval - `session_id` (this program's id, fixed) and `parent_id` in `Event` - `workspace.create / worktrees / remove` — `.. …

Source. PR #6 · `wiki-agent-5-agent-workspace`
