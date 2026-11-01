---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev Stage 5 — Hybrid search with graph expansion"
pr: 36
merged: 2026-09-27
branch: "feat/jev-stage-5-retrieval"
---

# feat: Jev Stage 5 — Hybrid search with graph expansion

What. Implements Stage 5 of `docs/plans/jev/5-retrieval.md`. It retrieves evidence that does not directly match the question by following relationships and fetches multiple sections of a document together. Instead of listing neighboring document titles, it returns the original text of the reached chunks and their paths.

Why. Retrieval contract(`tool/search/retrieval.py`): RetrievalRequest/Result(`retrieval-request/1`, `retrieval-result/1`). Source allowlists, filters, and visibility scopes are applied before ranking. The original question and the English question are ranked at once using BM25 and e5 (if the sentence is the same, only once), and RRF fusion is performed by chunk, not by page. Each allowed source is given a minimum slot, and sources with no results return that slot. Identical bodies are treated as a single candidate and hold different IDs together. - Scores are not mixed: `scores` keeps the ranks and scores of BM25, cosine, and RRF, as well as the graph rank and hop count separately. We do not create a weighted sum and call it relevance probability. …

Source. PR #36 · `feat/jev-stage-5-retrieval`
