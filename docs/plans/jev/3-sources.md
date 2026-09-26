# Stage 3 — ingest documents, memories, and papers

See the [overall design](0-overview.md). Prerequisite:
[stage 2](2-evidence-language.md).

Purpose. Route only to implemented sources and preserve origin, version,
coverage, and adoption reasons when research enters the wiki.

## SourceRecord contract

| Field | Meaning |
| --- | --- |
| source_id, repo_id, kind | Identity and scope shared with EvidenceChunk |
| origin | Canonical local path, external URL, or paper identifier |
| title, authors, published_at, fetched_at | Metadata; unknown values remain null |
| revision, content_hash | The edition and content actually read |
| authority | official, paper, repository, memory, or community |
| status | discovered, fetched, indexed, adopted, rejected, or unavailable |
| coverage | full_text, abstract_only, metadata_only, or partial |
| adoption | Adopted claims, scope, rationale, counterevidence, and validation conditions |
| visibility, license_note | Access scope and source information needed for retention/citation |

Authority is not correctness. Community material can be primary evidence for
its author's implementation; official documentation can become stale. Code
compares timestamps and editions. Abstract-only access never becomes full-text
coverage in downstream summaries.

## Implemented source families

| Source | Initial implementation | Searchable evidence |
| --- | --- | --- |
| Hub | Existing operator/craft catalog | Rule bodies and declared relationships |
| Documents | Selected repository's git-visible documents, explicit module and decision listings | Documentation, architecture, decisions |
| Memory | Existing saved summaries and decisions | Exclude raw transcripts; inherit deletion and visibility |
| Research | Explicitly adopted URLs and extracted snapshots | English chunks, original locations, adoption status |
| Papers | arXiv search/ID lookup and local PDF/Markdown registration | Abstracts and metadata; full text only after successful extraction |

Use the [arXiv API](https://info.arxiv.org/help/api/user-manual.html) as the first
paper provider. Recheck its current pagination, version, and service limits at
implementation time. Reuse installed extraction capabilities before introducing
a PDF dependency. Unsupported documents produce a visible extraction failure.

A URL fetcher is not whole-web search. Until a search provider is implemented,
the capability catalog advertises only paper search and explicit URL ingestion.
Do not claim access to paid full text or unsupported paper databases.

## Ingestion and adoption workflow

1. Refresh local sources through change events or request-time fingerprints.
2. Run external search only when required and the source is enabled.
3. Preserve discovered records, then index only content actually fetched.
4. Use Jev relevance to prioritize inspection; evaluate source claims and
   adoption conditions before treating a finding as repository knowledge.
5. Store the adoption record in runtime storage. Promote durable wiki changes
   through a specification, worktree change, and PR.
6. Preserve concise rejection and counterevidence records so future searches
   recover why an alternative was rejected.

Do not reproduce entire third-party documents as unsourced wiki prose.
Store adopted summaries, source locations, links, and applicability. A changed
URL snapshot creates a new revision rather than overwriting cited evidence.

## Network and write boundaries

Default to HTTPS. Reject credential-bearing URLs, file URIs, localhost,
private and link-local destinations. Revalidate every redirect and enforce
response-size, timeout, and content-type ceilings. DNS resolution and the actual
connection destination must agree with access checks; validating a hostname
before a second uncontrolled resolution is insufficient.

A link appearing in a document does not authorize an automatic fetch. Keep
provider keys out of URLs and traces. Disabled sources are excluded even if
cached. The server writes runtime data under the hub, not the original checkout.

## Files

`tool/search/sources.py` owns local catalogs and source records.
`tool/search/providers.py` contains concrete fetch/extraction functions, not
a speculative plugin system. Main's `knowledge.py` composes fetching,
normalization, and indexing.

Memory save/delete events trigger index updates. Source status and counts feed
stage 9. Explicitly test local ignored decision/module directories instead of
assuming git lists every permitted knowledge source.

## Completion gate

- Local documents, memory, and a real arXiv paper produce EvidenceChunk results.
- Abstract-only material never appears as evidence that full text was read.
- Duplicate URLs, revisions, deletion, disabled sources, and failed fetches behave correctly.
- Address restrictions, redirects, oversized responses, and cross-repository
  memory isolation are exercised.
- Research promotion produces a reviewable worktree diff without changing the
  original checkout.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Local sources | Document, memory, decision catalogs and deletion | Done |
| 2 | External sources | arXiv, explicit URLs, local files, coverage | Done |
| 3 | Adoption | Rationale, counterevidence, editions, wiki promotion | In progress — promotion commits in a worktree; routing it through a specification and its pull request is deferred to stages 8–9 |
| 4 | Verification | Live paper retrieval, isolation, errors, write boundaries | Done — `tool/test_sources.py`, a live arXiv lookup, and one live Jev grading of five arXiv abstracts (268 ms, 2,039 input tokens) |
