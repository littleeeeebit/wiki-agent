# Stage 2 — English evidence with original provenance

See the [overall design](0-overview.md). Prerequisite:
[stage 1](1-runtime-baseline.md).

Purpose. Standardize Jev's natural-language inputs on validated English
representations while every citation resolves to an unchanged original source.
Never judge a truncated excerpt as though it were the complete document.

## EvidenceChunk contract

```json
{
  "schema_version": 1,
  "repo_id": "repo-sha256",
  "source_id": "source-sha256",
  "revision": "content-sha256",
  "chunk_id": "chunk-sha256",
  "kind": "document",
  "locator": {"path": "docs/search.md", "start_line": 12, "end_line": 24},
  "heading_path": ["Search", "Routing"],
  "original_text": "Original passage",
  "text_en": "English passage",
  "language": "en",
  "translation": {"status": "original_english", "version": "v1"},
  "visibility": "repository"
}
```

Kinds are rule, document, decision, memory, research, and paper. Define separate
locator variants for a URL snapshot and PDF page/block. An unknown location
cannot be replaced with a fictitious line 1.

IDs derive from source identity, content, and normalization version. Identical
sentences from different sources remain distinct evidence. Separate display
paths from canonical paths used for access checks.

## Chunking and incremental indexing

Reuse `search.daemon.chunks()` heading and code-fence behavior. Split long
sections at paragraph boundaries. Preserve complete tables, lists, and code
where possible; explicitly label partial blocks. Keep oversized indivisible
blocks as additional-read targets.

Titles and heading paths provide indexing context but are not part of the
quoted original span. Original offsets are established before translation.
Never derive original line numbers from translated line counts.

Reuse translations and embeddings by content hash. Source edits and deletion
invalidate associated derived entries. A failed translation is not a successful
cache entry.

## English normalization

The existing `translate.translate()` returns original text on failure, so
string output alone cannot establish success. Add a public structured outcome
without breaking existing callers: text, status, model, prompt version, glossary
version, and protected-span validation.

| Input | Behavior |
| --- | --- |
| English prose | Reuse with original_english status |
| Korean prose | Reuse the translation engine and glossary; retain both representations |
| Names, code, paths, numbers, identifiers | Protect against loss, duplication, or substitution |
| Failed or uncertain language conversion | Explicit unavailable or uncertain status |
| Source contradicts its translation | Original governs; retire the translation from automatic decisions |

Main composes translation and indexing; search does not import translate.
Normalize corpus evidence during ingestion and questions/current state per turn.
Include negation, conditions, versions, and quantities in meaning-preservation
evaluation. CJK detection alone is not a language or translation-quality test.

On normalization failure, multilingual baseline retrieval can continue, but Jev
processing reports normalization_failed. Replace the prototype's whole-feature
fallback for state exceeding 4,000 characters with a structured state summary:
active specification, current revision, unresolved requirements, and recent
decisions. Include omitted-context references and prevent sufficiency claims
about information excluded from that summary.

## Persistence and atomicity

Store source/chunk records in the search index's SQLite database. Map them to
the existing vector cache without changing its cross-query meaning. A failed
source update preserves the previous generation.

Sharing identical cached representations never shares visibility. Deletion
must remove searchability and derived references, including cached English text
for private memory. Maintain a deletion journal if a crash interrupts cleanup.

## Implementation locations

- `tool/search/evidence.py`: contracts, validation, IDs, and locators.
- `tool/search/daemon.py`: storage and retrieval compatibility.
- `tool/translate/__init__.py`: explicit public translation outcomes.
- `tool/main/knowledge.py` and root CLI: composition reused by later stages.
- `tool/test_evidence.py`: provenance, mutation, deletion, isolation, and failures.

## Completion gate and rollback

Every English, Korean, and mixed-source result resolves to its real original
span. Protected-span corruption must be zero. Edit, rename, and deletion tests
must not return obsolete chunks. Human-authored expected meanings, rather than
the translating model's own approval, establish semantic test labels.

Build the new schema in a separate generation. Rollback selects the previous
index; rebuilding never requires altering authoritative documents or memories.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Contract | EvidenceChunk and original locators | Done — PR #33: `tool/search/evidence.py`. Repo, source and chunk ids are derived from content. File, URL-snapshot and PDF locators, with no invented line 1. Validation refuses English that was never actually produced |
| 2 | Chunking | Long sections, protected spans, and hashes | Done — PR #33: `search/daemon.py` splits long sections at block boundaries. Fences stay whole and are marked `oversized`; cut tables and lists are marked `partial`. Each chunk keeps its exact original lines. Symlinks that point out of the repository are not evidence |
| 3 | Normalization | Structured outcomes, caching, and failure paths | Done — PR #33: `translate.english()` returns original_english, translated, unavailable, uncertain or retired, with the reason and the model, prompt and glossary versions. A changed number, identifier or placeholder, or output still in Korean, is `uncertain`. A reply is checked before it is cached. A question with no English is `normalization_failed` and falls back to baseline retrieval. Long state becomes a structured summary that names what it omitted. `translate()` is unchanged |
| 4 | Updates | Incremental indexing, deletion, generation publication | Done — PR #33: the SQLite `Store` syncs incrementally in one transaction. It keeps a generation per chunker, publishes a generation whole with rollback to the one it replaced, and only the publishing transaction prunes. A deletion journal removes a private memory's text, vectors and English. Private English and vectors never enter a shared cache |
| 5 | Verification | Citation identity, meaning, isolation, recovery | Done — PR #33: `tool/test_evidence.py` (36 cases): edits, renames and deletions; stale reads; private deletion; generations and rollback; builds interrupted mid-way; protected spans. `eval/jev/meaning.json`, human-labelled: 8 of 8. Live: all 748 chunks of this repository resolve to their original spans, and a Korean question reached Jev as English |
