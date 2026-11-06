---
scope: project
severity: preference
triggers: []
domain: ''
title: "#103 Follow-up: Add termination function to agent public list (lint)"
pr: 106
merged: 2026-10-10
branch: "fix/agent-shutdown-export"
---

# #103 Follow-up: Add termination function to agent public list (lint)

What. After merging #103, fix one item caught by wiki lint (`lint.py`). `tool/main/app.py` directly imported `agent.chat_session`, which is outside the public list (`__all__`) of the `agent` package.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #106 · `fix/agent-shutdown-export`
