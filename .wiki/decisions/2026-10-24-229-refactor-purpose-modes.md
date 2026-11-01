---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring Purpose Mode 1/3 — Read-only Investigation"
pr: 229
merged: 2026-10-24
branch: "refactor-purpose-modes"
---

# Refactoring Purpose Mode 1/3 — Read-only Investigation

What. The quick cleanup → module restructuring → full-scale refactoring in the refactoring tab were stages stacked according to risk level. Therefore, it was impossible to choose what to do (whether it is organizing the structure while preserving behavior, removing an old contract, or fundamentally fixing the structure/performance). Also, there was no stage to read and explain the repository by pipeline unit and then divide it into executable specifications.

Why. The mode was not a purpose but a cumulative risk stage. The stage of reading code and the stage of changing code were mixed within a single execution.

Source. PR #229 · `refactor-purpose-modes`
