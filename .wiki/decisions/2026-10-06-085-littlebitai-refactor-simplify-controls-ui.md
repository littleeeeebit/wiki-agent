---
scope: project
severity: contract
triggers: ["대화\\s*(모델|프롬프트|응답|생성)", "응답\\s*(정책|수리|스키마)", "페르소나", "말투", "gemini", "openai"]
domain: dialogue
title: "Refactoring conversation integration and removal of unnecessary execution limits"
pr: 85
merged: 2026-10-06
branch: "LittleBitAI/refactor-simplify-controls-ui"
---

# Refactoring conversation integration and removal of unnecessary execution limits

What. Place refactoring immediately to the right of the conversation's 'next task', and execute the existing three modes as the 'start' of the specification defined in the conversation. Independent tabs have been removed, and candidate investigation and execution status have been integrated into the conversation card.

Why. Removed target file count, time, invocation, and token limits and their corresponding interruption conditions from refactoring execution and full-scale refactoring plans. Confirmed usage is displayed only in details, and missing token usage is kept as unknown. Mode changes allow re-examination of unexecuted specifications while maintaining the conversation, and if purpose or mode expansion is required, it presents options and stops. Reuses existing test fixing, candidate competition, review/approval, and manual merge procedures. Verification: - Refactoring, conversation connection, and actual candidate runner tests passed. - 34 general improvement runners and 16 evaluation adapter tests passed; confirmed that limits for general tasks are maintained. - Specification regression tests, frontend build and lint, wiki lint, debt ratchet, and no UTF-8 BOM confirmed. …

Source. PR #85 · `LittleBitAI/refactor-simplify-controls-ui`
