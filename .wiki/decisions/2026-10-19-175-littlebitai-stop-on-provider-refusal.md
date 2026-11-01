---
scope: project
severity: contract
triggers: ["대화\\s*(모델|프롬프트|응답|생성)", "응답\\s*(정책|수리|스키마)", "페르소나", "말투", "gemini", "openai"]
domain: dialogue
title: "Safe refusal stops without retransmission and continues with a new conversation (task/review) · Re-presenting specifications after compression"
pr: 175
merged: 2026-10-19
branch: "LittleBitAI/stop-on-provider-refusal"
---

# Safe refusal stops without retransmission and continues with a new conversation (task/review) · Re-presenting specifications after compression

What. #67 The R2 revision turn of the review loop stopped at `R2 수정 완료를 확인하지 못했다 — Error running remote compact task: This request was blocked by our safety systems. Reason: Potentially unintended activity.`.

Why. The code for this reason is `misalignment_policy_violation` — it is not content censorship but a judgment that "the agent is attempting to do something unintended by the human," and it looks at the entire thread flow. An automated message that is not human (discovery by another AI) causing an irreversible remote history rewrite is the most likely scenario for R2 (OpenAI does not disclose triggers, so it cannot be confirmed). Reports that refused threads cannot be continued are repeated (openai/codex #42523·#44355, pingdotgg/t3code #10362). Resuming the same thread is blocked every time. Long tasks involve multiple compressions, and each time, the specifications become blurred within the summary (user observation).

Source. PR #175 · `LittleBitAI/stop-on-provider-refusal`
