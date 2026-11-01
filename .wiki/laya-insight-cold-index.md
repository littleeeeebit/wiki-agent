---
scope: project
severity: preference
triggers: ["(?i)laya.*(generat|capture|생성기).*(unavailable|daemon|index|색인)"]
reads: [laya/generate.py, tool/search/__init__.py]
slots: []
sources: []
links: [laya, laya-exp-useful-data]
---

# A cold search index makes every generator capture fail as unavailable

Part of [laya](laya.md). Found 2026-10-14 while preparing the diverse
generator for the ten retrieval kinds.

## Claim

The generator asks only a search daemon that is already running, and never
builds an index itself (`laya/generate.py`, `captured`). When the daemon has
not indexed a repository, it answers nothing. Every capture there then ends
`unavailable: retrieval_unavailable`, even though the daemon passes the
readiness check.

## Evidence

- On 2026-10-14, a capture of "Where does the search daemon keep its
  index?" failed in this repository and in ai-coding-wiki. The same capture
  had given 13 passages on 2026-10-13.
- The readiness check (`searching()`) returned true the whole time.
- After `search.ask(..., wait=300)` in this repository, the same capture
  gave one grade-evidence request with 41 questions.

- Warming all eight corpus repositories took 293, 59, 1, 1549, 31, 140, 55
  and 2 s. The longest was ai-companion-voice-agent, at about 26 minutes.
- After warming, the diverse generator's capture (branch
  `feat/laya-diverse-gen`, no model calls) was tried with one lookup query
  and one compound query in each repository. All 16 runs gave a route
  request and a grade-evidence request:
  - Every run had 11–13 passages, so 11–13 questions each for `useful`,
    `conflict` and `redirect`.
  - Every run had 3 `source` questions.
  - `ask` came only from compound queries, 3 per query.
  - `coverage` gave 1 per lookup query and 3 per compound query.
  - `repair` appeared in 12 of the 16 runs.

## How to apply

Before a repository's first query, the generator asks the daemon to finish
that repository's index, waiting up to 30 minutes (`WARM`). A run that cannot
get the index stops with `search_unavailable` and records no query as empty.
Before a paid run over the eight corpus repositories, warm them all first
with `search.ask(query, repo, wait=1800, start=False)`.
