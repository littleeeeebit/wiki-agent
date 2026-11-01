---
scope: project
severity: contract
triggers: ["대화\\s*(모델|프롬프트|응답|생성)", "응답\\s*(정책|수리|스키마)", "페르소나", "말투", "gemini", "openai"]
domain: dialogue
title: "External Audit P1 Fixes: Question ID, branch deletion race condition, PR unpublishing, raw conversation exclusion, decision evidence extraction"
pr: 101
merged: 2026-10-10
branch: "fix/audit-p1-boundaries"
---

# External Audit P1 Fixes: Question ID, branch deletion race condition, PR unpublishing, raw conversation exclusion, decision evidence extraction

What. We performed a full verification of the items pointed out in the external audit (2026-10-10, based on 8ba8eaa) and fixed the confirmed P1 bundle.

Why. R1: When the same `ChatSession` restarts the provider due to model changes, etc., `session_id` is maintained, but Codex assigns new request numbers for each process. Therefore, the answer to a previous question card could be delivered to a new question with the same number. R3: If an external CLI commits between the `merged` judgment and the `worktree remove` execution, `branch -D` was executed based on the old judgment. The new test fails in the pre-fix code (branch deleted). R4: The docstring promised "verification immediately before PR publishing," but verification only occurred before translation. The new test fails in the pre-fix code (PR created). K1: `. …

Source. PR #101 · `fix/audit-p1-boundaries`
