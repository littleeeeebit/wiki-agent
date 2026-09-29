# PR 3 — explicit wiki ownership and retrieval scope

Keep one repository, with distinct navigation and retrieval applicability for
product maintenance, hook consumers, and Jev maintenance.

## Work

Publish an ownership map before moving files. Reuse existing documents as
authorities: product development/architecture under docs, hook installation and
shared operator/craft contracts, and a Jev maintenance guide linking code,
configuration, graph diagnostics, policies, evaluation and rollback.

Distinguish audience/topic metadata from repository identity and access control.
A Jev maintenance page may be relevant to product maintainers; that does not make
it relevant to every connected repository. Keep target knowledge in its .wiki.
Shared rules remain shared once, rather than copied into all three areas.

Add retrieval filters/defaults using existing source metadata where possible.
Support explicit cross-scope lookup with provenance. Preserve source and citation
identities when navigation changes. Document the policy graph, repository document
map, evidence knowledge graph, and workflow transition graph separately.

Correct misleading current documentation; mark superseded archived descriptions
historically rather than rewriting what was verified. Account for SessionStart's
two-plan display limit so this overview remains discoverable without pretending
older unfinished work disappeared.

## Evidence and exit

A product question, hook-consumer question and Jev-maintenance question retrieve
the expected authority. Cross-repository private evidence never leaks. Shared
hook rules still inject regardless of model routing. Broken links and citation
regressions fail the applicable checks.

## Scope and rollback

No repository split, duplicated wiki engine, or broad move-only diff. Remove new
filters/navigation independently if their relevance behavior is wrong.

## Implementation blueprint

### Authoritative documentation map

| Audience | Authority to maintain | Content |
| --- | --- | --- |
| Product maintainer | Existing `docs/development.md`, new `docs/architecture.md` | Pipelines, process/state ownership, local development and final-gate flow |
| Connected-repository operator | Existing `docs/hooks-setup.md`, `docs/chat-setup.md` | Installation, real host event checks, adapter lifecycle, troubleshooting |
| Jev maintainer | New `docs/jev-maintenance.md` | Decision coverage, graph types, data revisions, diagnostics, calibration and rollback |
| Shared behavior | Existing `operator/`, `craft/` | Rules that apply across projects; no project-specific copied values |
| Target knowledge | Target `.wiki/` and `docs/` | Target architecture, decisions, memories and adopted research |

Add links in root `index.md` and README. Preserve old paths where possible.
The Jev guide links to the current plan and evaluation artifacts; it does not copy
calibration values into an independently maintained table.

### Retrieval contract

Proposed additive field inside the existing request filters object:
`audiences: ["product", "hooks", "jev"] | null`. This is relevance metadata,
not a security label. Existing `repo_id`/`visibility` remain authoritative.

For the hub, maintain an explicit small path-to-audience map in
`search.sources`; for target documents leave audiences unclassified by default.
Known hub paths use the table above; Jev source/plan paths are Jev+product;
operator/craft are shared and eligible under every audience filter. Unknown paths
remain eligible as unclassified rather than disappearing silently. Record that
fallback in retrieval diagnostics.

Prefer deriving audiences from source path in the snapshot instead of changing
chunk IDs or embedding text. Add the optional filter to `retrieval.request`
validation and the existing eligibility function before BM25/vector ranking,
source-floor allocation and graph expansion. A node excluded from candidates must
not leak through a bridge path. Tests verify both seed and expanded candidates.

An unfiltered request preserves current behavior. A selected task/maintenance
context supplies a filter; Jev may choose among code-offered audience scopes only.
Explicit broader lookup relaxes audience filtering, never repository isolation.
Do not read another project's private store to satisfy it. Hook consumers gain
links to installation docs; this PR does not automatically ingest hub-private
maintenance documents into every project.

### File and API changes

| Location | Change |
| --- | --- |
| `tool/search/sources.py` | Central audience derivation from canonical relative paths |
| `tool/search/retrieval.py` | Validate and apply optional audiences throughout retrieval |
| `tool/main/knowledge.py::prepare` | Carry requested audience through first round and repairs |
| `tool/main/query.py::Say` | Optional validated audience field; default preserves old clients |
| `web/src/lib/api.ts` | Mirror request field; context selector sends it |
| `docs/`, `index.md`, `README.md` | Navigation and single-authority links |

No new graph/index generation is required if audiences are derived at request
time; if persistence is chosen during implementation, version the derived index
and rebuild atomically without changing source/citation identity. Do not migrate
authoritative Markdown just to rename a metadata field.

### Acceptance scenarios

Create a fixture containing hub shared rules, hook setup, Jev maintenance, product
architecture and another repository's private page. Query each audience and assert
that relevant classified pages/shared rules remain, wrong-audience classified pages
are excluded, unclassified fallback is explained, and private pages never enter.
Repeat with the wrong-audience page reachable via a graph edge.

Check citations resolve after navigation edits and SessionStart still lists new
documents. Keep the existing two-plan display cap; the new overview links explicitly
to the old unfinished loop/Jev plans so discovery does not imply completion.
Rollback removes optional audience filters without losing indexed text.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Map | Map authorities and scopes | Done — `docs/architecture.md`, `docs/jev-maintenance.md` |
| 2 | Wire | Wire navigation and retrieval applicability | Done — `filters.audiences`, screen scope selector |
| 3 | Verify | Verify isolation, links and rule injection | Done — fixture tests in `tool/test_retrieval.py` |

## Implementation notes

- Audiences are derived from the path when the index loads (`sources.audiences`),
  not persisted: no index version, no rebuild, no chunk id change.
- A blocked chunk's walk status is `audience`, apart from `filtered`, so a
  diagnostic tells relevance from access.
- Explicit broader lookup is a request without `audiences`; Jev is offered no
  audience choice in this PR.
- SessionStart's document list reads `.wiki/corpus.json`, a per-checkout file:
  a checkout lists new documents after `python tool/corpus.py --project <repo> --write`.
