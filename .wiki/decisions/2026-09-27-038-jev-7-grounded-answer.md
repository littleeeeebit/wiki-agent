---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev stage 7 — claims, citations and controlled publication"
pr: 38
merged: 2026-09-27
branch: "jev-7-grounded-answer"
---

# feat: Jev stage 7 — claims, citations and controlled publication

What. Stage 7 of `docs/plans/jev/`: in active mode, an answer reaches the reader only as claims that code and Jev have checked against the retrieved evidence.

Why. Draft (`tool/main/knowledge.py` `Grounding`, `tool/prompts/answer-draft.md`): the answering session writes one `answer-draft` block over run-owned evidence ids. `AnswerDraft` (`answer-draft/1`) holds source_fact, inference, recommendation and direct_text claims. …

Source. PR #38 · `jev-7-grounded-answer`
