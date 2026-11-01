---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Define document authority and narrow search to reader scope (Reliability Level 3)"
pr: 49
merged: 2026-09-29
branch: "feat/reliability-3-wiki-scopes"
---

# feat: Define document authority and narrow search to reader scope (Reliability Level 3)

What. There are three readers within one repository: product maintenance, repository operators connecting hooks, and Jev maintenance, but neither the documentation nor the search recognized that distinction. Project isolation (`repo_id`, `visibility`) already existed, but it could not express "who the document is for." Reliability Plan Level 3 (`docs/plans/reliability/3-wiki-scopes.md`).

Why. Document authority - New `docs/architecture.md`: Document map (one authority document per reader), pipeline, process and state ownership, explaining the four graphs (policy graph, repository document map, evidence knowledge graph, workflow transition graph) separately, Search scope rules - New `docs/jev-maintenance.md`: Code with decisions, configuration, data revisions, diagnostic commands, and corrections are linked to Level 10 documents (values are not copied), rollback switch table - "Where to read" table in `README.md`, links to `index.md`·`docs/development.md`. …

Source. PR #49 · `feat/reliability-3-wiki-scopes`
