# Stage 4 — build a knowledge graph that retrieval can use

See the [overall design](0-overview.md). Prerequisite:
[stage 3](3-sources.md).

Purpose. Connect documents, chunks, entities, and decisions through traceable
source evidence. Stage 5 must use these relationships to recover evidence that
does not directly match the question. Node growth alone is not success.

## Graph schema

| Node kind | Example | Identity |
| --- | --- | --- |
| source | Document, memory, paper | Source ID and revision |
| chunk | Evidence section | EvidenceChunk ID |
| entity | Explicit module, setting, feature, or named paper subject | Repository/source scope, normalized name, type |
| decision | An actual recorded choice and rationale | Existing decision ID or original span ID |

| Edge kind | Construction | Retrieval meaning |
| --- | --- | --- |
| contains, next_chunk | Document structure | Section context |
| links_to, reads | Existing Markdown/front-matter parsers | Explicit references |
| mentions | Candidate extraction and span validation | Other evidence concerning an entity |
| depends_on | Source-supported technical relationship | Dependency evidence candidate |
| supersedes | Explicit replacement decision or edition relation | Current versus historical decisions |
| contradicts | Comparison with provenance on both sides | Evidence that must be examined together |
| co_injected | Existing observations | Exploration hint, never factual support |

KnowledgeEdge contains edge_id, repo_id, from_id, to_id, kind, directed,
source_spans, source_revisions, origin, confidence, extractor_version, and
status. Origins are deterministic, extracted, or observed. Traversing an edge
backwards does not reverse its factual meaning.

## Construction sequence

Reuse `repo_graph.targets/resolve`, hub links, and reads declarations for
structural edges. Store derived relationships in the index; do not rewrite
authoritative Markdown. Endpoints must resolve to registered evidence.

A generative model proposes entities and relations in structured output.
Jev cannot generate new entity names or relation prose. Code checks mention
spans, then Jev evaluates whether the proposed relation is supported. Use the
stage 1 decision transport; stage 6 supplies the final common policy contract.

Uncertain edges remain candidates and are not strong default traversal edges.
Compare bounded pairs sharing an explicit entity, link, or decision topic;
never perform an all-pairs corpus comparison. Separate identical names by scope
and type, and require evidence before alias merging.

## Supersession and contradiction

Newer timestamps alone cannot revoke decisions. Supersession requires an
explicit source relation or repository-owned decision status. Date arithmetic
belongs in code. Preserve conflicting sources rather than merging their claims
into an invented consensus.

Editing a source revision invalidates its extracted edges until revalidation.
Deleting an endpoint removes dangling edges. If another independent source
supports the same relationship, preserve that provenance without retaining the
deleted source's claim.

## Storage and map compatibility

`tool/search/knowledge_graph.py` owns SQLite nodes and edges. Index outgoing
and incoming access by repository, endpoint, and kind. Publish rebuilt graphs
through the same generation mechanism as evidence.

`repo_graph.picture()` remains read-only and consumes a projection. Distinguish
document-level presentation from chunk-level retrieval detail. Stage 9 shows
which relationships were actually traversed. Existing graph.json files remain
compatible display artifacts rather than becoming the primary search database.

## Evaluation fixtures

Include a document-to-module-to-decision bridge, same-named modules in separate
repositories, contradictory decisions, unsupported generated edges, edited and
deleted sources, and directional dependencies.

Deterministic edges must match expected endpoints exactly. Measure extraction
precision and recall separately; a larger graph cannot compensate for false
relations. Freeze a labeled subset before adjusting extraction prompts.

## Completion gate and rollback

Every adopted edge has valid original spans. There are zero dangling,
out-of-scope, or fabricated deterministic endpoints. Rebuilding one snapshot
preserves stable IDs and deterministic edges. Reuse extracted results only when
model, prompt, policy, and normalization versions match.

Demonstrate incremental edit/delete handling, crash recovery, and a read-only
map. On failure, restore the prior generation. Retiring semantic edges must
leave structural links and baseline retrieval operational.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Structure | Source/chunk nodes and explicit links | Not started |
| 2 | Semantics | Entity/relation candidates and support validation | Not started |
| 3 | Revisions | Supersession, contradiction, invalidation | Not started |
| 4 | Projection | Map compatibility and graph query API | Not started |
| 5 | Verification | Precision, updates, isolation, reconstruction | Not started |
