---
scope: project
severity: preference
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Fixed ghost follow-up waiting for finished turns, poller stops during merge, and loss of intermediate steps during focus switching"
pr: 102
merged: 2026-10-10
branch: "LittleBitAI/fix-task-process-visibility"
---

# Fixed ghost follow-up waiting for finished turns, poller stops during merge, and loss of intermediate steps during focus switching

What. (P0) Defect where finished task turns remain "in progress," causing the review loop to hang: The server counted background completion notifications already consumed within the turn (queue `remove` in Claude CLI logs) as "a follow-up response is coming" and waited forever. The worktree lock was not released, so the review loop waited at `wait_hold` until a human stopped it. Now, if there are no tasks running after a response and the CLI is quiet for 30 seconds, the turn is closed with that response. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #102 · `LittleBitAI/fix-task-process-visibility`
