---
scope: project
severity: preference
triggers: []
title: "Adopt Jev for bounded retrieval decisions"
---

# Adopt Jev for bounded retrieval decisions

Decision. Use English Jev questions for retrieval routing, passage relevance,
and evidence sufficiency. Python owns source access, validation, conservative
thresholds, and one wider retry. Preserve uncertain and contradictory evidence;
fall back to baseline search on failure. Hook rules remain independent.

Research and implementation contract:
[Jev retrieval controller](../../docs/research/jev-retrieval-controller.md).
Primary sources include TypeSafe's API and RAG cookbooks, the practitioner
`jev-reranker` implementation, LangGraph's retrieval graph, and Microsoft's
GraphRAG local-search design. The cited findings live in the linked record.

Verification. No TypeSafe API key was present during implementation. Simulated
responses establish code behavior, not live model quality or calibrated thresholds.
