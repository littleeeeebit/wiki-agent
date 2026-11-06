---
scope: project
severity: preference
triggers: []
domain: ''
title: "At the start of refactoring, define the investigation, implementation, and review models and fix them to all specifications of that investigation."
pr: 232
merged: 2026-10-24
branch: "refactor-roles"
---

# At the start of refactoring, define the investigation, implementation, and review models and fix them to all specifications of that investigation.

What. When I pressed [Start Refactoring], the investigation started with an unintended model (`codex:gpt-6-astra`) (investigation `29df65a9` of `ai-companion-voice-agent`, canceled at the base commit gate stage).

Why. [Start Refactoring] did not ask for a model separately, but took the model selected in the chat toolbar at that time as the investigation model. The execution model was re-selected for each specification card, and the review model followed the global review settings. Therefore, if the toolbar or settings were changed, the model changed between stages without notice.

Source. PR #232 · `refactor-roles`
