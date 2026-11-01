---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Token Diet Step 5 — Search Daemon tool/search/ (without hook assistance)"
pr: 19
merged: 2026-09-25
branch: "token-diet-5-search"
---

# feat: Token Diet Step 5 — Search Daemon tool/search/ (without hook assistance)

What. Step 5 of the token diet, first of four stacked PRs (5 → 6 → 7 → 8). Review and merge in order; each later PR is based on the one before and is rebased onto `main` after its predecessor merges.

Why. `tool/search/` — a pipeline package (added to `lint.PIPELINES`, `__all__` is the contract) - `daemon.py` — one per machine. Chunks at `##`/`###`, BM25 (English words + Hangul bigrams) and `multilingual-e5-small` cosine merged by RRF. BM25 alone while the model downloads or without `requirements-search.txt`. Ends itself after 3 idle hours. - `__init__. …

Source. PR #19 · `token-diet-5-search`
