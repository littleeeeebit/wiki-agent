---
scope: project
severity: preference
triggers: []
domain: ''
title: "fix: add MODEL and watching to the public entry point of translate"
pr: 51
merged: 2026-09-29
branch: "fix-translate-public-names"
---

# fix: add MODEL and watching to the public entry point of translate

What. Add `MODEL` and `watching` to `translate.__all__`.

Why. In #50, `main.knowledge` started calling `translate.MODEL` (the model name in the call record) and `translate.watching` (checking if the question split request was actually sent). Since both are outside `__all__`, `tool/lint.py` produced 3 public entry point violations after the merge. The review round only ran the affected tests, and this lint was missed because it is not in the full gate. - `python tool/lint.py --repo .`: No new findings - `ruff check tool/translate/__init__. …

Source. PR #51 · `fix-translate-public-names`
