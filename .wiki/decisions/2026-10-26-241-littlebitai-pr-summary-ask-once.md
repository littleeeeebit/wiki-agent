---
scope: project
severity: preference
triggers: []
domain: ''
title: "The title and body of the work PR are uploaded as a Korean summary written by the worker after reviewing the diff, rather than a translation of the specification."
pr: 241
merged: 2026-10-26
branch: "LittleBitAI/pr-summary-ask-once"
---

# The title and body of the work PR are uploaded as a Korean summary written by the worker after reviewing the diff, rather than a translation of the specification.

What. Specification sentences (goal, keep, problem, risk, rollback, decisions) are no longer uploaded to the work PR. The stage of machine-translating the PR body (`korean()`) has also been removed. - The `pr-summary` (`title`·`changes`·`why`·`review`) written by the worker just before reporting completion is the only text in the PR. The worker writes this block directly in Korean after reviewing the final diff (`tool/prompts/work-spec. …

Why. Even after changing the PR structure with #240, ai-companion-voice-agent #262 was uploaded as a slop. The cause was not the format, but the source of the sentences. The specification is the plan and instructions given to the worker. If translated, it becomes an imperative sentence like "Add a helper," keep becomes a constraint, and risk becomes a prediction before implementation. None of these are "what has changed." During the conversation, a repair instruction was added to the goal as `spec-update`, and "Restore to the exact form of 565f1b4 and..." was uploaded as the PR change summary. As English dense with terminology was translated sentence by sentence, 'proactive' was split into "선제적" and "사전 예방적" within the same PR. The `pr-summary` of #240 silently reverted to specification sentences if the format was incorrect. …

Source. PR #241 · `LittleBitAI/pr-summary-ask-once`
