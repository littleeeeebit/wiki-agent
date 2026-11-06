---
scope: project
severity: preference
triggers: []
domain: ''
title: "Show the JSON shape in the directive so that full refactoring does not stop because it cannot read the refactor-scope block."
pr: 227
merged: 2026-10-24
branch: "fix/refactor-scope-json-contract"
---

# Show the JSON shape in the directive so that full refactoring does not stop because it cannot read the refactor-scope block.

What. When running full refactoring in ai-coding-wiki, it stopped at the audit stage with "`refactor-scope` block could not be read — Expecting value: line 1 column 1 (char 0)" (`raw/refactor/runs/ai-coding-wiki/44204e21.json`).

Why. The directive `refactor.purpose()` requesting this block only said "Return a refactor-scope block containing reason and choices." There was no mention of JSON, nor the shape the server reads (`question` + `options[].label`, `specs._shape("choices")`). Therefore, the model wrote `reason:` / `choices:` in YAML, and the `json.loads` of `fenced()` failed at the first character. Other directives (`improvement_host.py`) requesting the same block already specify JSON and its shape.

Source. PR #227 · `fix/refactor-scope-json-contract`
