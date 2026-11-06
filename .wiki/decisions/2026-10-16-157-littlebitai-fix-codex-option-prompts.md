---
scope: project
severity: preference
triggers: []
domain: ''
title: "Receive Codex options as a choices block"
pr: 157
merged: 2026-10-16
branch: "LittleBitAI/fix-codex-option-prompts"
---

# Receive Codex options as a choices block

What. Fix the P0 defect where almost no options appeared when running the implementer or next task with Codex.

Why. Results of comparing `raw/work` and `raw/chat/next.jsonl` records: Question tool calls in implementation session: 135 out of 368 turns for Claude, 14 out of 136 turns for Codex. `choices` block for next task: 41 out of 103 turns for Claude, 1 out of 5 turns for Codex. In the remaining turns, Codex decided on its own and issued the specification immediately. Since Codex did not ask even in the next task where text blocks were already being used, I included "when to ask" in the instructions rather than just changing the format. Reason for including it in developer instructions instead of a SKILL file: Because Codex decides for itself whether to read the SKILL, the defect of not asking remains.

Source. PR #157 · `LittleBitAI/fix-codex-option-prompts`
