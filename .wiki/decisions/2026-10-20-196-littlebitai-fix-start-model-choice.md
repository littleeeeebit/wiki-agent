---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix the specification card to send the model selected in the empty agent slot at the start."
pr: 196
merged: 2026-10-20
branch: "LittleBitAI/fix-start-model-choice"
---

# Fix the specification card to send the model selected in the empty agent slot at the start.

What. The selection for the empty agent slot is stored in the project-specific key `new:<repo>`. - `시작` sends the selection of the empty slot if the specification itself has no selection. - A started task inherits the model actually used in the first turn as its own selection.

Why. When clicking `시작 ▸` on the specification card, the first turn ran with the Claude default instead of the model selected in the empty agent slot (e.g., `codex:gpt-6.1-sol` · high) (P0). In #194, task models were divided by task-specific keys (`spec:<repo>/<id>`). The model selected in an empty agent slot without a selected task is stored in an empty key (`''`), but `시작` only read the key for that specific specification. Since that key was empty, it sent `{model: '', effort: ''}`, and the server created a session with the CLI default (Claude). Actual record: The first turn of ai-coding-wiki `luna6-and-embedding-small` ran with `provider: claude`. …

Source. PR #196 · `LittleBitAI/fix-start-model-choice`
