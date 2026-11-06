---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya joint: views from a support run's picks on every split"
pr: 168
merged: 2026-10-18
branch: "feat/laya-joint-views"
---

# Laya joint: views from a support run's picks on every split

What. `support.evaluate(..., split=)` reads any coverage split. - `train.py pack --picks <support checkpoint zip>` records `picks` in packed. …

Why. Joint labels on views built from support labels said no to 16 of 18 covered requirements (the unlabelled listed passages complete them); on run 211003's picks they agreed with coverage 68/72. Training on the views serving asks keeps train and serving the same (owner, 2026-10-18: "Creating actual picks in Colab").

Source. PR #168 · `feat/laya-joint-views`
