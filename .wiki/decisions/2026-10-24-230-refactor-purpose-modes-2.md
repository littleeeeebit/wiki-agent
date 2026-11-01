---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring objective mode 2/3 — Execute investigation specifications as normal tasks"
pr: 230
merged: 2026-10-24
branch: "refactor-purpose-modes-2"
---

# Refactoring objective mode 2/3 — Execute investigation specifications as normal tasks

What. The investigation for PR 1 creates a specification card before starting, but there was no path yet to execute that card, go through review/verification, and reach a PR. If the previous method's dedicated refactoring pipeline is reused, it diverges from the work tree, review, and verification flow of normal tasks.

Why. There was no place for investigation specifications in the normal task flow (`specs.start` → work tree → agent → gate → push/PR → review loop) where one specification is executed as one PR.

Source. PR #230 · `refactor-purpose-modes-2`
