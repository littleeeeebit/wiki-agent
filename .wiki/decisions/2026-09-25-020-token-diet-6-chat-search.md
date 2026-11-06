---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: token savings step 6 — wiki chat searches first and reads only the necessary lines"
pr: 20
merged: 2026-09-25
branch: "token-diet-6-chat-search"
---

# feat: Token diet step 6 — wiki chat searches first and reads only the necessary lines

What. Step 6 of the token diet, second of four stacked PRs. Based on #19 (step 5); after #19 merges this is rebased onto `main` and retargeted.

Why. `python tool/search` lists, under each hit, the pages one hop away (hub `graph.json` `links`, repository `.wiki/graph.json` `edges`), with the hub node's headline and rule or the document's title and lead from `corpus.json`. Each neighbour once. - `tool/main/query.py`: every focus's system prompt ends with the search command, built from this server's Python and the selected repository. …

Source. PR #20 · `token-diet-6-chat-search`
