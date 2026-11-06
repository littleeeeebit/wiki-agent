---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Allows turns interrupted during a task due to model capacity to continue instead of ending in failure."
pr: 221
merged: 2026-10-23
branch: "fix/capacity-cut-mid-turn-continues"
---

# Allows turns interrupted during a task due to model capacity to continue instead of ending in failure.

What. For turns interrupted after starting a task with "Selected model is at capacity", send a message(`chat_busy.CONTINUE`) to the same session after the same wait (increasing from 30 seconds up to 600 seconds) saying "Continue, do not repeat completed steps". Previously, that error was output as the result of the turn, ending the turn. - Turns rejected before the task are resent as the same request as before. If the message to continue is rejected before the task, that message is resent. …

Why. Today (2026-10-23), the Codex coding turn of the ai-coding-wiki was interrupted twice within one task (11:21, after 5,255 steps / 11:35, after 4,455 steps, `raw/work/ai-coding-wiki/luna-deepseek-creativity-comparison.jsonl`). When a human sent "Continue." at 11:22, the same session continued and finished up to the commit — the session was alive and only the instruction to continue was missing. #180 only retried rejections before the task.

Source. PR #221 · `fix/capacity-cut-mid-turn-continues`
