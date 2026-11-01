---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring investigation creates one specification per pipeline for stages 1 and 2, and one specification per cause for stage 3."
pr: 243
merged: 2026-10-26
branch: "LittleBitAI/refactor-spec-unit"
---

# Refactoring investigation creates one specification per pipeline for stages 1 and 2, and one specification per cause for stage 3.

What. In functional preservation structure cleanup (stage 1) and unnecessary complexity cleanup (stage 2), one specification is one pipeline. All technical debt that can be handled in that pipeline is contained in one specification. Shared modules also have one specification per path. - Location: `refactor_survey.INTEGRATE_PROMPT`, `refactor_survey.SPEC_SCHEMA` - If stage 1 or 2 investigation produces two or more specifications for the same pipeline, the server rejects it. ...

Why. Refactoring investigation reads the repository by pipeline unit and creates specification cards to be executed in the final integration stage. However, in the ai-companion-voice-agent investigation, 19 pipelines and 4 shared modules were divided into 46 cards. One card usually dealt with one function, and most code changes in merged PRs were under 70 lines (e.g., #249 4 lines, #260 20 lines, #262 69 lines). This is because the integration stage directive caused the creation of "one specification per technical debt," and the server check only had an upper limit of "no more than two pipelines." The original intention was one specification per pipeline. A card for a single function requires each card to go through tasks, behavior fixation tests, reviews, and CI once each. ...

Source. PR #243 · `LittleBitAI/refactor-spec-unit`
