---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya joint: Ask about 'parts' together to reduce coverage indecision"
pr: 167
merged: 2026-10-18
branch: "feat/laya-joint"
---

# Laya joint: Ask about 'parts' together to reduce coverage indecision

What. New kind `joint` (`laya/joint.py`): In a view that keeps only the paragraphs read as 'parts' by support, ask "Do these paragraphs together state the entirety of requirement r?" (yes/no). Use the view saved in `kinds.LISTED` as is. - coverage answer: Use the yes/no from support as is, and if it is undecided, use the answer from joint. …

Why. The coverage created by support is 0.842 for the 52% decided, which is the best so far, but the remaining 48% is left to the LLM. Even if rules are applied to the support label itself, only 78 out of 219 are decided. This is because out of 103 satisfied requirements, 55 have no 'entirety' paragraph, and multiple paragraphs state 'parts' separately (`.wiki/laya-exp-retrieval-training.md`, run 20261017-211003). Therefore, even if support is further augmented, these questions will not be decided. The owner chose the 'sum of parts' step (2026-10-18).

Source. PR #167 · `feat/laya-joint`
