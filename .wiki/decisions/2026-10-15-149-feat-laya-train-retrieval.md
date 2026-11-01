---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya Colab: Sequentially train 9 types of labeled search in one zip"
pr: 149
merged: 2026-10-15
branch: "feat/laya-train-retrieval"
---

# Laya Colab: Sequentially train 9 types of labeled search in one zip

What. Bundle and fix the notebook to sequentially train 9 types of search (analysis, ask, conflict, progress, redirect, route, source, useful, coverage) with a single Colab execution.

Why. All 9 types have over 1,000 training labels. The current notebook only handles one type at a time, requiring 9 separate zip files to be created, uploaded, and run, and `MAX_LEN` only contained `useful`, making it impossible to train the remaining types.

Source. PR #149 · `feat/laya-train-retrieval`
