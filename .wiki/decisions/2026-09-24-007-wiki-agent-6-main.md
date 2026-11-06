---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Ask the wiki, assign tasks to the agent in the worktree, and use the shell in one window"
pr: 7
merged: 2026-09-24
branch: "wiki-agent-6-main"
---

# feat: Ask the wiki, assign tasks to the agent in the worktree, and use the shell in one window

What. Step 6: `docs/plans/wiki-agent-6-main.md`.

Why. `tool/main/`: New main. Move the channel of the old `chat.py` to the five focuses of the wiki query, and attach the worktree (`workspace`) and the write session (`agent`). Call the four pipelines only as `__all__`, and lint checks them - from answer to task: `/api/draft` creates a draft containing the question, answer, citation `file:line`, applied rules, open plans, and recent decisions. The last line is written by a human - the two write buttons for retrospective candidates go to the same draft instead of the `oneshot` that wrote to the original without approval (user decision) - an approved answer is sent only if the `session_id` of the session that asked matches, and the path passed by the screen opens only if it is in the list of `workspace` …

Source. PR #7 · `wiki-agent-6-main`
