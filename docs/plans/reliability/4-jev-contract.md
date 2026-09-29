# PR 4 — Jev transition ownership and graph health

Make every server-owned decision explainable: current state, legal candidates,
chosen transition, deciding component, evidence and cost.

## Work

Extend existing decision coverage rather than add a controller framework. Identify
deterministic edges, Jev choices, generation steps, and host-owned tool decisions.
Preserve code authority over allowed actions, revisions, permissions and budgets.

Trace start -> gather -> route -> retrieve -> grade -> answer and refused review
-> gather -> fix. If retrieval was already selected, prevent a second retrieval-
need decision from cancelling or needlessly re-choosing it; source selection and
support assessment remain separate. Record legitimate reasons for another decision,
such as changed state or new evidence. Inspect host tool events for duplicate search.

Inventory Jev calls, drafting/repair, query decomposition, graph extraction,
explanation and translation under their actual owners. Add to existing tracing;
unknown cost is unknown, not zero. Version all behavior-affecting decisions,
including ASK and ANALYSIS, without silently treating provisional policies as fitted.

Expose existing graph verification with generation/revision and scope: invalid or
dangling edges, unresolved spans, out-of-scope edges, adoption status and extraction
versions. Capture a current graph snapshot. A structural pass is not a semantic
quality result; useful traversal is measured in PR 5.

## Evidence and exit

Focused recorded/replayed flows show no routine LLM duplicate of a Jev transition,
correct stale-state handling, and preserved authority. A poisoned graph fixture
detects invalid references; a real snapshot has an inspectable report. Fix actual
health defects at source; do not suppress them in the report.

## Scope and rollback

Retain host-internal tool autonomy as an explicitly uncovered boundary. No model
call per graph edge. Reuse SQLite, tracing and current decision records. Policies
remain reversible through existing settings.

## Implementation blueprint

### Decision ownership table to expose

Extend `main.decisions.coverage()` with retrieval decisions and explicit uncovered
host choices. Each item carries `point`, `owner`, `allowed_operations`,
`authority`, `policy_version` and `provisional`. No secrets or raw state enter
this status response.

| Point | Owner / rule |
| --- | --- |
| Work first step / refused-round preparation | Jev selects a code-offered operation |
| Retrieval already requested by an admitted action | Code requires retrieval; Jev chooses sources/strategy |
| Evidence grade / sufficiency / support | Jev evaluates evidence, not execution permission |
| Deterministic next step, exhausted budget, stale authorization | Code |
| Draft / research / explanation | Configured generative role |
| Arbitrary tools within that role's session | Host; trace, but do not claim server control |

### Avoid repeated retrieval-need judgment

Add a proposed optional `cause` record to `knowledge.prepare`:
`{action_id, point, operation, state_revision}`. Pass `require=True` from
`decisions.gathered` after an admitted retrieve_evidence action. Changing this flag
alone is insufficient: change `route_questions`/Flow.route so the route request
omits the `retrieve` question when required. It still asks source selection and
applicable analysis/requirement questions. The state machine must not index a
missing `verdicts["retrieve"]`; trace `retrieval_required_by_action` instead.

Keep ordinary query behavior when cause is absent. Invalid/stale proposals are
re-admitted at the existing boundary before retrieval. A failed gather records
failure or no evidence under current rules; it must not falsely claim no retrieval
was required. PR 8 adds stricter stop-on-failure only for mandatory recovery.

### Call record and versions

Extend existing run/action tracing with:
`call_id, parent_call_id, purpose, owner, input_digest, state_revision,
provider, model, started_at, duration_ms, token_usage, cost_usd, cost_known,
retry_of, outcome`. Purposes form a closed list: route, source_select, grade,
sufficiency, support, normalize, decompose, draft, explain, graph_extract, research.
A transport batch with multiple questions is one provider call with multiple
purposes, not several bills. Cache hits are explicitly non-provider operations.

Keep raw state in existing private run records only where already allowed. Use the
existing telemetry export boundary to permit safe fields; adding this record must
not export prompts/source bodies accidentally. Unknown CLI or provider cost stays
null with cost_known false; totals expose known subtotal and incomplete count.

Add a behavior manifest containing hashes for ASK, ANALYSIS, PROMPTS, grounding,
normalization and graph extraction alongside fitted policy IDs. Do not relabel old
fitted artifacts as covering new prompts. Cache identity and evaluation identity
both reference the actual behavior; policy fitting validity is per decision.

### Graph report

Reuse `knowledge.check_graph`/`knowledge_graph.verify`; add a proposed read-only
`GET /api/knowledge/graph/health` for the selected authorized repository, returning
`{repo_id, generation, checked_at, versions, counts, violations, status}`.
For this endpoint do not silently rebuild/extract or pay for a model call. Add an
existing-snapshot-only mode to index opening; an absent snapshot returns
`status: "not_indexed"`. Source changes during checking yield stale, not healthy.

Violations include existing invalid/dangling/out-of-scope/unresolved spans; counts
include adopted/candidate/observed origins. Do not call a graph healthy solely
because it contains zero edges. Return structural status and a separate
semantic_evaluation reference (possibly absent).

### Acceptance and rollback

Replay one direct query, one admitted retrieval action, one missing-evidence repair
and one review context request. Assert one necessary retrieval decision, no second
host route instruction, preserved sources/support calls, correct cache accounting,
and no secret export. Test health on absent, valid, dangling, stale-source and
cross-scope snapshots. Existing graph/evidence tests remain the starting point.

Read old records with missing fields as unknown. New writers use the new version;
no bulk mutation of historical traces. Revert routing changes independently of
read-only observability. PR 5 must evaluate the corrected current manifest.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Inventory | Inventory and trace routing/call ownership | Done — `decisions.coverage()` items, `tracing.call` records |
| 2 | Correct | Correct redundant routing and behavior versions | Done — `prepare(cause=)`, `knowledge.behavior()`, `KIND_VERSIONS` |
| 3 | Graph health | Surface and verify graph health | Done — `knowledge.graph_health`, `GET /api/knowledge/graph/health` |

## Implementation notes

- Retrieval required by a caller — an admitted action's `cause`, or an
  answer's return to retrieval — drops only the `retrieve` question from the
  route request. Ordinary questions, including ones the `EXPLICIT` pattern
  matches, still ask it. A tape recorded with `required: true` before this
  change no longer replays: its route answered a question no longer asked.
  The committed smoke tape was recorded without it and still matches.
- The draft and analysis prompts now say the server already searched; the
  host's own search commands during a drafting turn are counted
  (`host_searches`), not blocked. Host tool autonomy stays uncovered.
- The routing policy artifact covers `route`, `source`, `useful`, `coverage`,
  `conflict`, `redirect` and `repair`; `ask`, `analysis` and `action` are
  provisional and reported so. The fitter does not fit `ask` or `analysis`
  yet; when it does it must write `kind_versions`.
- Jev and the translator report no cost, so their calls are `cost_known:
  false`. A host turn's cost is known when the CLI reports `cost_usd`.
- `Index.refresh(sync=False)` / `local_index(existing=True)` read a store
  without syncing, embedding or rebuilding the graph; `search.published`
  refuses a missing or unpublished store before one is opened, since opening
  creates it. `Store.drift` finds changed and new files the way `sync` would.

### Graph snapshot, 2026-09-29

This repository, hub `wiki-agent`, read by `python tool/relations.py
--project . health`:

- Before refreshing: `stale` (`sources_changed`) — 9 changed and 22 new files
  since the last index. All 483 unresolved spans lay in the 9 changed files,
  so they were staleness, not a graph defect.
- After `relations.py check` refreshed the index (no model call):
  `healthy`, generation 1 — 1,263 nodes, 2,429 edges, all adopted; 2,412
  deterministic (`contains` 1,103, `next_chunk` 907, `links_to` 402) and 17
  extracted `mentions`; no invalid, dangling, out-of-scope or unresolved
  edge. No extracted `depends_on` edge is adopted, so the semantic layer is
  thin: whether traversal helps is PR 5's measurement.

## Sources

[TypeSafe router](https://github.com/TypeSafeAI/typesafe-router),
[confidence routing](https://docs.typesafe.ai/patterns/confidence-routing), and
[GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/).
