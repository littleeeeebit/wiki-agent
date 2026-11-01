---
scope: project
severity: preference
triggers: []
domain: ''
title: "Asking Jev questions in the way the Jev document speaks — 11-step phrasing and comparison tool"
pr: 182
merged: 2026-10-19
branch: "jev-question-shape"
---

# Asking Jev questions in the way the Jev document speaks — 11-step phrasing and comparison tool

What. `tool/eval/arms.py` creates requests for each branch, asks within the token limit, and compares the old and new in the same case based on the cost of compliance with existing policies.

Why. Our questions are caught exactly where the Jev model document (jev-1.13 jaggedness) states it is weak. In one judge request, there are a median of 28 questions and the state is about 15,000 characters. The questions point to the inside of the state by id ("passage p3", "requirement r1"). The conflict stands on a negated exception clause, and the source condition is ambiguous. The repair option order is fixed, and negations are overlapped in ask and analysis. Costs are revealed in the policy compliance results. For conflict, 49 out of 151 cases are uncertain, and for source, 113 out of 192 cases are uncertain. The plan is `docs/plans/jev/11-question-shape. …

Source. PR #182 · `jev-question-shape`
