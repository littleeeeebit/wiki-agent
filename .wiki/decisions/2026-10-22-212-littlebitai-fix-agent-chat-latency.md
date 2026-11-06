---
scope: project
severity: contract
triggers: ["대화\\s*(모델|프롬프트|응답|생성)", "응답\\s*(정책|수리|스키마)", "페르소나", "말투", "gemini", "openai"]
domain: dialogue
title: "Removing input lag in the agent box — The issue where the entire conversation was re-laid out for every key"
pr: 212
merged: 2026-10-22
branch: "LittleBitAI/fix-agent-chat-latency"
---

# Removing input lag in the agent box — The issue where the entire conversation was re-laid out for every key

What. `Agent.tsx`, `Stream.tsx`: Applied a `contain: size` to the conversation scroll container. By having the height received from the column rather than the content, the input was prevented from reaching the conversation layout. - `Answer.tsx`: Wrapped with `memo`. When the screen is updated (at every event of the running turn), the markdown of unchanged answers is not re-parsed.

Why. There was significant lag every time a character was typed in the agent box (P0). As a result of direct measurement in the running app: the DOM of the agent box was approximately 60,000 nodes. Every time the text or height of the input box changed, the entire conversation scroll container next to it (a sibling of the flex column) was re-laid out. One forced layout took about 230ms. As a result, one key took 565~1,120ms, and it took about 15 seconds to type 19 characters.

Source. PR #212 · `LittleBitAI/fix-agent-chat-latency`
