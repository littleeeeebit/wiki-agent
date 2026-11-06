---
scope: project
severity: preference
triggers: []
domain: ''
title: "Modification: Agent real-time diff, chapter-based questions, and usage display"
pr: 62
merged: 2026-10-02
branch: "fix/agent-panel-questions-usage"
---

# Modification: Agent real-time diff, chapter-based questions, and usage display

What. Fixes the issue where file changes were not visible on the agent screen, question options were small, and were limited to a single question. It displays changes in real-time based on the commit at the start of the task, groups questions into chapter-based cards, and provides explanations, examples, multiple selections, and direct input. It connects the existing Korean display feature to all questions, while keeping the submitted values as the original.

Why. Adds initial connection time, token usage, usage limit, and reset time to the Claude/Codex connection status. Values not sent by the provider are displayed as unverified. Claude limit information is limited to the events actually received. Upon reconnection, it displays the provider information of the actually connected session instead of the model selection value. If Codex initialization fails, it reclaims child processes, and large diffs are limited at the reading stage to prevent accumulating all changes in server memory. - Final commit `e1377b2` Full Python verification: 1,317 passed, 1 skipped, 1 existing warning. Took 17 minutes and 59 seconds. - 250 related regression tests passed. Confirmed failure when removing each fix in process leak, provider misdisplay, and diff limit regression tests. …

Source. PR #62 · `fix/agent-panel-questions-usage`
