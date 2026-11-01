---
scope: project
severity: preference
triggers: []
domain: ''
title: "Prevent instructions from being sent from old question cards in cells without a work tree"
pr: 200
merged: 2026-10-21
branch: "LittleBitAI/fix-stale-pane-send"
---

# Prevent instructions from being sent from old question cards in cells without a work tree

What. In `Agent.tsx`, the question card of the last answer sends an answer only in cells with a work tree row. If there is no row, it appears inactive like an input cell (`busy`). Question cards for tasks with rows remain as they are.

Why. This is an existing landmine pointed out in the #199 review. When a task is finished, the selection remains on the original checkout path. The left list hides that row, but the right agent cell continues to show the old conversation for that path. This cell has no work tree, so the input cell is inactive, but the question card of the last answer (`choices`) was still pressed. Clicking it sent a new instruction to that original checkout. The reviewer confirmed that a `{"path":"fixture-task","text":"Continue old task", ...}` request was being sent from the browser.

Source. PR #200 · `LittleBitAI/fix-stale-pane-send`
