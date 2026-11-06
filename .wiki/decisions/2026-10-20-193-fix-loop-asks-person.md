---
scope: project
severity: preference
triggers: []
domain: ''
title: "Review loop: stop and wait when the wiki-agent asks, and continue on its own after answering"
pr: 193
merged: 2026-10-20
branch: "fix/loop-asks-person"
---

# Review loop: stop and wait when the wiki-agent asks, and continue on its own after answering

What. `loop.turn`: if a loop's work-cell turn ends on a well-formed `choices` question (`specs.asks`), stop with the new `Why.ASKED` (`작업 에이전트의 질문`). Nothing is resent. …

Why. rfg_rag #76 R7 hit this sequence: 1. The Codex repair turn asked the person with a `choices` block (2 options). Codex asks by ending its turn. 2. The loop read this as "repair not finished", resent the correction text at once, then stopped as `라운드 형식`. The person saw this as a time limit. 3. The person picked an option in the agent pane. …

Source. PR #193 · `fix/loop-asks-person`
