---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev Stage 4 — Knowledge graph available for search"
pr: 35
merged: 2026-09-27
branch: "feat/jev-stage-4-knowledge-graph"
---

# feat: Jev Stage 4 — Knowledge graph available for search

What. Implement Stage 4 of `docs/plans/jev/4-knowledge-graph.md`. Connect sources, chunks, entities, and decisions with relationships, and ensure every relationship has the original text segment that serves as evidence for it. This is the foundation for Stage 5 to retrieve evidence that does not directly match the question by following relationships.

Why. Graph storage (`tool/search/knowledge_graph.py`): KnowledgeEdge contract. Nodes, edges, and segments are stored in the evidence repository's SQLite by generation. Thus, the graph is published along with chunks, preserved for rollback, and cleaned up together. The index is rebuilt only when the input changes, and only the changed rows are written. Even after a rebuild, the IDs remain the same. - Structure: Clauses (`contains`, `next_chunk`), Markdown/`[[위키]]` links, `links`/`reads` in front matter, decision records. Supersedes only appear in specified `supersedes`/`superseded_by`. A later date alone does not supersede anything. …

Source. PR #35 · `feat/jev-stage-4-knowledge-graph`
