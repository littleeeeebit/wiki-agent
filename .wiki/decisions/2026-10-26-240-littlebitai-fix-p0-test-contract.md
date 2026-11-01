---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fixed two issues where refactoring tasks stopped right before the PR, and changed the task PR title and body to focus on 'what has changed'."
pr: 240
merged: 2026-10-26
branch: "LittleBitAI/fix-p0-test-contract"
---

# Fixed two issues where refactoring tasks stopped right before the PR, and changed the task PR title and body to focus on 'what has changed'.

What. The refactoring investigation no longer uses the specification to "extend existing tests". This instruction was blocked by the test contract gate. Workers will now create new tests if they receive such instructions. - If the PR title exceeds 256 characters, it will be truncated before submission. Previously, GitHub rejected it, causing tasks that passed all gates to stop. - Before reporting completion, the worker writes a `pr-summary` block (title, changes, review points). This block becomes the PR title and "summary of changes". …

Why. The refactoring task `proactive-eligibility-wiring-7357ebbe` of ai-companion-voice-agent was blocked three times right before the PR. 1. Changes outside the test contract: The `tests.add` of the `preserve` specification instructed to extend existing tests. The worker followed this, but the gate blocks if the existing test body is changed. The `preserve` specification could not even unlock that test with `tests.contract`, so the specification could not pass from the beginning. 2. PR title exceeded: The goal (1,085 characters) of the specification was put into the title in its entirety, which GitHub rejected. 3. Unreadable PR: Both the title and the "summary of changes" were the plan sentences of the specification ("Extract ~. Move to ~. …

Source. PR #240 · `LittleBitAI/fix-p0-test-contract`
