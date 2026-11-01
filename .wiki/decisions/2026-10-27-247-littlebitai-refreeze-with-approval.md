---
scope: project
severity: preference
triggers: []
domain: ''
title: "When a human reviews and approves the changes to a behavior fixation test that has finished fixation in step 1, it is checked against the code before the change and then fixed again."
pr: 247
merged: 2026-10-27
branch: "LittleBitAI/refreeze-with-approval"
---

# When a human reviews and approves the changes to a behavior fixation test that has finished fixation in step 1, it is checked against the code before the change and then fixed again.

What. When the step 1(`preserve`) card is stopped, a [View Fixation Test Changes] button appears in the stop box of the review tab(`web/src/components/Review.tsx`). - If you click it, it shows the changes to the fixation test that occurred after the fixation point. - If you click [Approve and Fix Again], the server checks the changes and fixes it again. - The server(`tool/main/refactor_run. …

Why. The step 1 card `preserve-repository-boundaries-1f9c4293` (PR #270) of ai-companion-voice-agent is stuck with no way out. 1. The required markings for repository rule tests were missing from 5 behavior fixation tests. 2. If you add the markings to the fixation test, it stops with "fixed test changed". 3. If you add an exception to the rule test, it stops with "change outside of test contract". You cannot place a test contract in the step 1 specification. It is correct that the server intentionally views the addition of `pytestmark` as a test change. This is because inserting a skip marking in the same place could skip the entire test. Therefore, instead of the machine judging the changes, a human reads the changes and approves them. …

Source. PR #247 · `LittleBitAI/refreeze-with-approval`
