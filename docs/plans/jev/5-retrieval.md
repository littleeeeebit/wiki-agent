# Stage 5 — hybrid retrieval with bounded graph expansion

See the [overall design](0-overview.md). Prerequisite:
[stage 4](4-knowledge-graph.md).

Purpose. Retrieve indirect evidence through relationships and recover multiple
necessary sections from one document. Listing neighbour titles does not satisfy
graph-based retrieval.

## Retrieval contracts

RetrievalRequest contains repo_id, query_original, query_en, source_allowlist,
filters, generation, limit, deadline, graph_budget, and seen_chunk_ids.
Filters cover source kind, edition/time restrictions, and visibility. Source
selection happens before the top-k cutoff.

RetrievalResult contains chunks, paths, scores, coverage, missing_sources,
truncated, generation, and elapsed_ms. Preserve BM25, cosine, RRF, and graph
discovery information separately. A discovery score is not a support probability.

## Algorithm

1. Validate repository identity and registered source IDs.
2. Search original and English queries using existing BM25 and multilingual-e5;
   avoid duplicate work when they are identical.
3. Keep chunk-level RRF candidates instead of immediately choosing one section
   per page.
4. Allocate minimum source coverage inside the total candidate budget, returning
   unused quota when a source has no results.
5. Traverse bounded chunk/source/entity/decision relationships from top seeds.
6. Fetch the discovered chunks' actual text, score query relevance, and preserve
   path and seed provenance.
7. Deduplicate chunks and near-identical passages while retaining seeds and
   bridge evidence for the stage 6 grader.

Keep RRF and add a separately inspectable graph candidate lane. Do not invent an
uncalibrated weighted sum and label it relevance probability. Record ranks and
scores from each lane.

## Traversal budget

Initial defaults: eight seeds, at most two hops, fan-out five per visited node,
and 40 unique candidate chunks total. Record all values in the evaluation
manifest. The visited set includes source revision. High-degree nodes are
preselected using explicit edge priority and lexical relevance.

Every hop checks visibility. A document in repository A pointing at repository B
does not grant access to B. Repeated searches inherit visited IDs and the same
total deadline and candidate allowance.

## Targeted retrieval repair

Sending the same query with the same candidate count again is not improvement.

| Missing evidence | Next operation |
| --- | --- |
| Omitted source family | Search remaining enabled sources |
| Indirectly named subject | Add a discovered bridge entity as query/graph seed |
| Multiple question requirements | Generate up to three scoped subqueries |
| Missing section context | Fetch bounded adjacent sections |
| Required external evidence | Use an implemented, enabled research/paper provider |

The generator may propose subqueries, but code and the decision workflow verify
that version constraints, exclusions, and user intent remain intact. Generation
consumes the same overall budget. Start with at most three retrieval rounds,
including the initial round.

## Implementation locations

Extend `search/daemon.py` with chunk-level internal results.
`search/retrieval.py` owns fusion, expansion, and deduplication.
Preserve the basic `search.ask()` and CLI output contract; expose a versioned
structured result through the public search API.

Main owns cross-pipeline orchestration. The daemon must not perform slow model
calls while holding its shared index lock. Acquire a stable generation snapshot,
release locks, and perform bounded downstream work.

## Completion gate

A bridge fixture whose answer passage shares no direct query terms must retrieve
that passage with graph traversal enabled. Returning baseline results plus
neighbour titles fails this gate.

Exercise source selection before cutoff, multiple chunks per document, cycles,
fan-out, deleted nodes, large blocks, cancellation, and repository boundaries.
Compare seed recall with graph expansion disabled; expansion must not displace
known supporting seeds merely to fill a graph quota.

Record successful and failed graph discoveries with their paths. Stage 10 owns
the final recall, answer-quality, latency, and cost acceptance thresholds.

## Rollback

A graph-off switch restores the current hybrid ranking while retaining readable
trace history. Old CLI clients continue receiving valid path/line results.
New schema results never silently masquerade as the old page-level format.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Candidates | Chunk RRF and source coverage | Done — `tool/search/retrieval.py`: the RetrievalRequest/Result contract (`retrieval-request/1`, `retrieval-result/1`); sources, filters and visibility applied before ranking; original and English queries ranked by BM25 and e5 in one batch, fused per chunk; a floor per allowed source, an empty one's given back; identical text kept once with every id. `daemon.Index` gives per-chunk scores (`cosines`) and a snapshot read outside the daemon's lock; `/retrieve` beside the unchanged `/search` |
| 2 | Expansion | Seeds, hops, fan-out, and provenance | Done — `retrieval.expand`: adopted edges only, `contains` not walked (a source stands for its chunks, a decision for its record's), priority then BM25 to preselect; eight seeds, two hops, fan-out five, 40 candidates across rounds; every hop checked like a seed; visited chunk ids carry their revision; every path recorded as discovered, corroborated, seen, deleted, refused or over budget; the graph lane appended after the seeds, ordered by hops, seed rank, then relevance, never summed with RRF. A snapshot walks `knowledge_graph.Frozen`, the adopted graph read in one transaction, since a refresh rewrites rows inside a generation. The 40-candidate allowance is the request's `max_candidates`, spent by seeds, adjacent sections and the graph lane alike, and split between sibling subqueries |
| 3 | Repair | Missing-evidence-driven source and query expansion | Done — `retrieval.repair`: remaining enabled sources, reached entities as graph seeds, checked subqueries (versions kept, exclusions kept, at most three), adjacent sections, the external families; each round inherits seen ids, deadline, generation and allowance, at most three rounds. `main.knowledge.repair` asks a model for subqueries (`prompts/retrieval-subqueries.md`) and arXiv for papers only when the caller allows it, both inside the run's budget. `WIKI_GRAPH_RETRIEVAL=off` is the switch |
| 4 | Verification | Bridge recall, cost, isolation, cancellation | Done — `tool/test_retrieval.py` (26 cases): a passage sharing no word with its question reached through a link and not without, seeds identical with the graph on, per-source floors before the cut, two sections of one page, cycles, fan-out and the allowance, a node gone from the snapshot, an oversized block, cancellation and the deadline, refused sources and visibility, the hub as another repository, a stale generation, an entity bridge, every repair, the switch, the daemon walking outside its lock. `tool/eval/retrieval.py` on `eval/jev/bridge.json`, no API call: recall@8 0.714 with the graph off, 0.929 on, seeds identical 8/8, the link, `reads` and `supersedes` bridges gained, the prose-only bridge missed and recorded; BM25 and e5 hybrid alike, 3–5 ms a query. On this repository (`repo-smoke.json`, hybrid, 117 files): recall 1.0 both ways, seeds identical 5/5, 17–24 ms with the graph, and the walk spends the whole 40-candidate allowance in round 1 (five `budget` refusals) |

Not built in this stage: the controller (`search/controller.py`) still retrieves pages through `ask`; moving the app's question path onto `retrieve` and choosing which repair to run is stage 6's state machine. Stage 6 must also split the shared allowance between rounds: with the defaults, round 1 on a real repository leaves a repair round no room. Edition filters read a record's fetch time only; a local file is its current revision. Near-identical means identical after whitespace and case; no shingle comparison.
