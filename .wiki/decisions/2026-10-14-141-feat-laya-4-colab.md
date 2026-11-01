---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya Stage 4: Colab (L4) training preparation, generator question error handling, oneshot cleanup failure fix"
pr: 141
merged: 2026-10-14
branch: "feat/laya-4-colab"
---

# Laya Stage 4: Colab (L4) training preparation, generator question error handling, oneshot cleanup failure fix

What. Colab training (Owner decision 2026-10-14: Training is Colab L4) - `laya/train.py pack <kind>` zips the `laya/` code and its type labels (excluding candidates, pools, and tests). - `train.train` receives public checkpoints (`base`·`typed`) as reviewed commits (`PINNED_REVISIONS`) for training. `laya.train. …

Why. The training equipment has changed from RTX 3080 to Colab L4. Since the data is local-only, a path to bundle and upload it is required. Medium-scale generation stopped at a question containing a Korean identifier, and labeling died during the Codex turn cleanup.

Source. PR #141 · `feat/laya-4-colab`
