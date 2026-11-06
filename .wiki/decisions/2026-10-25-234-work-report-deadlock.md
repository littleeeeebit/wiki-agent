---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "If the task does not submit a completion report after finishing the commit, request it once, and instruct that the gate item is the server's responsibility."
pr: 234
merged: 2026-10-25
branch: "work-report-deadlock"
---

# If the task does not submit a completion report after finishing the commit, request it once, and instruct that the gate item is the server's responsibility.

What. Instruction: The first completion item is written as the server's gate. For this item, you should write `"pass": true` as the evidence for the intensive inspection. The server runs the gate immediately after reporting, and does not trust the report before it passes. Therefore, the fact that the gate is not yet ready is not a reason to delay the report. - Server safety net (`specs._check`): If the turn sent by the server meets all the following conditions, request a completion report once. If there is no report on the second attempt, write the reason and stop. …

Why. Refactoring card `qwen-unused-elapsed-helper` for ai-companion-voice-agent was executed by the Codex (gpt-6-astra) worker. The changes were committed to `139a3b63` and 39 intensive tests also passed. However, it ended the turn by saying, "I cannot submit a passed done-report because the server's full pytest gate is not yet ready." The task instruction (`work-spec.md`) contains three sentences together. - ① Do not run the server's round commands directly. - ② If there is no result yet, it is not a pass. - ③ If there are items that have not passed, do not submit a done-report. The first item of the completion condition is that very server gate. Therefore, a model that follows the instructions literally cannot submit a report. …

Source. PR #234 · `work-report-deadlock`
