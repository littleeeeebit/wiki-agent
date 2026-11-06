---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev Stage 3 — Collecting documents, memory, papers, and adopted research"
pr: 34
merged: 2026-09-26
branch: "feat/jev-stage-3-sources"
---

# feat: Jev Stage 3 — Collecting documents, memory, papers, and adopted research

What. Implement Stage 3 of `docs/plans/jev/3-sources.md`. Set paths only to existing sources, and preserve the original location, edition, coverage, and reasons for adoption or rejection for each source.

Why. Local sources: Moved the file list from the daemon to `tool/search/sources.py`. Even if ignored by git, explicitly read `.wiki/decisions/`, `.wiki/modules/`, and `.wiki/memory/`, and do not read raw conversations (`.raw.md`) from memory. When memory is saved, it is indexed immediately without waiting for the next question. - SourceRecord: Keep a record for each external source. The actual content read is placed in the user cache as a snapshot named by content hash, and is not written to the checkout. If the re-fetched content is different, it becomes a new edition, and the previous snapshot remains so that old citations are still resolved. Disconnected sources are neither searched nor fetched. …

Source. PR #34 · `feat/jev-stage-3-sources`
