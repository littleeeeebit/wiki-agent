---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya support and joint: quote the requirement in the question"
pr: 197
merged: 2026-10-20
branch: "laya-direct-wording"
---

# Laya support and joint: quote the requirement in the question

What. `support.question(state, rid, pid)` and `joint.question(state, rid)` quote the requirement's text (`Requirement r0 asks: "…".`), as Jev's adopted stage 11 arm B does for coverage. Before, they named it only by id. - `support. …

Why. Jev's arm B halved its policy cost by stating the condition in words. Laya was never asked whether it has the same weak spot. Labels do not change with the wording, so the local splits' stored questions are reworded (git-ignored data, old files kept as `*.v-ids.jsonl`), and one Colab run retrains support and joint on the same data as the shape3 runs. …

Source. PR #197 · `laya-direct-wording`
