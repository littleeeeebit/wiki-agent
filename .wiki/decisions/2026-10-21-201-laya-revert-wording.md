---
scope: project
severity: preference
triggers: []
domain: ''
title: "Revert Laya's quoted-requirement wording (#197); record the result"
pr: 201
merged: 2026-10-21
branch: "laya-revert-wording"
---

# Revert Laya's quoted-requirement wording (#197); record the result

What. Reverts the code of #197: `support.question(rid, pid)` and `joint.question(rid)` name the requirement by id again; `support.asks` and `trimmed`'s rebuilt question go with it. - `docs/plans/laya/5-augmentation.md` keeps #197's plan note and adds the result.

Why. Colab run 20261020-215126, paired against the shape3 runs on questions both scored (bootstrap over states): | Kind | Before → after | Gain | Noise floor | | --- | --- | --- | --- | | support accuracy | 0.857 → 0.869 | +0.012 | 0.014 | | coverage from support 211003 + joint | 0.767 → 0.712 | -0.055 | 0.049 | Support's gain is under the floor; joint's loss is past it. …

Source. PR #201 · `laya-revert-wording`
