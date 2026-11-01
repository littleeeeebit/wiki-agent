---
scope: project
severity: preference
triggers: []
domain: ''
title: "Phase 1 refactoring investigation makes the entire repository into one specification sheet."
pr: 244
merged: 2026-10-26
branch: "LittleBitAI/preserve-one-spec"
---

# Phase 1 refactoring investigation makes the entire repository into one specification sheet.

What. Phase 1(preserve) investigation now makes the entire repository into one specification sheet. The pipeline value of this specification is `all`, and the preservation debt of all pipelines and shared modules goes into this one sheet. If there are two or more specification sheets, the check will reject it and return it once with the reason to merge them into one sheet. - Phase 2(simplify) is one sheet per pipeline as it is now, and Phase 3(rework) is one sheet per cause. …

Why. After #243, when I re-investigated the ai-companion-voice-agent by pipeline unit, 26 cards came out, and most of them were 1-2 code files. This is because debt that does not change behavior is usually within one core file. Therefore, it is cheaper to run the task, review, and CI once than 26 times. This is the method chosen by the user.

Source. PR #244 · `LittleBitAI/preserve-one-spec`
