---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev Phase 2 — English evidence with original source"
pr: 33
merged: 2026-09-26
branch: "jev-2-evidence-language"
---

# feat: Jev Phase 2 — English evidence with original source

What. Implements Phase 2 of `docs/plans/jev/2-evidence-language.md`. The natural language read by Jev is aligned to verified English, and all citations revert to the unchanged original text segments.

Why. Evidence contract: EvidenceChunk has been defined in `tool/search/evidence.py`. Repository, source, and chunk ID are created from the content, and the location is in one of three forms: file line range, URL snapshot, or PDF page/block; unknown locations are not filled with line 1. Chunks that look like English but for which English has not been generated are rejected during verification. - Chunk splitting: Long clauses are split at block boundaries. Code fences are kept intact as `oversized`, and truncated tables and lists are marked as `partial`. The chunk body is the original text line as is, and the title path is only for index context and is not included in the citation segment. - Storage: SQLite `Store` next to the vector cache. Incremental updates are a single transaction, so if it fails, the previous state remains. For each chunk method...

Source. PR #33 · `jev-2-evidence-language`
