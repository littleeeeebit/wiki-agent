---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya support: Augmentation that re-asks the 'all' label in a different form"
pr: 158
merged: 2026-10-16
branch: "feat/laya-support-all"
---

# Laya support: Augmentation that re-asks the 'all' label in a different form

What. When aug1 and aug2 (all remaining 2–5 rank paragraphs) were added, the 'some' recall rose to 20/42. Instead, 'all' collapsed to 2/17, so the coverage created by this model never judged 'yes' even once (run 20261016-210840). In the train set, there are 953 'some' but only 472 'all'. This time, I will significantly augment 'all'.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #158 · `feat/laya-support-all`
