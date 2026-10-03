# Architecture — what runs, who owns what, and where to read

For whoever maintains this program. Local setup and the checks are in
[development](development.md); this page says how the parts fit and which
document is the authority for each question.

## Documentation map

One repository, three maintenance audiences, one authority per question.
Shared rules are written once, in the hub, and never copied into an area.

| Audience | Authority | Content |
| --- | --- | --- |
| Product maintainer | This page, [development](development.md) | Pipelines, process and state ownership, local development and the final gate |
| Connected-repository operator | [hooks setup](hooks-setup.md), [chat setup](chat-setup.md) | Installation, real host event checks, adapter lifecycle, troubleshooting |
| Jev maintainer | [Jev maintenance](jev-maintenance.md) | Decision coverage, graph types, data revisions, diagnostics, calibration and rollback |
| Shared behaviour | `operator/`, `craft/` ([index](../index.md)) | Rules that apply across projects, no project-specific values |
| Target knowledge | The target's own `.wiki/` and `docs/` | Its architecture, decisions, memories and adopted research |

Plans under `docs/plans/` record why and how something was built; the pages
above say how it is now. A finished series moves to `docs/plans/done/` and is
kept as history, not rewritten.

## Pipelines

The packages under `tool/` are pipelines whose imports `tool/lint.py` checks
(`PIPELINES`); `tool/common/` is below all of them.

| Package | Owns |
| --- | --- |
| `tool/common/` | Settings reader, budgets, process options; imports no pipeline |
| `tool/wiki/` | Hub pages, their front matter and matching |
| `tool/workspace/` | Task branches, explicit linked checkouts and host session records |
| `tool/translate/` | English normalization and the Korean overlay |
| `tool/search/` | Sources, chunks, the evidence store, the knowledge graph and retrieval |
| `tool/decision/` | Jev transport, typed decisions, policy and claims; no retrieval or execution |
| `tool/agent/` | Generative models and host sessions |
| `tool/main/` | The program: composes the others into the query, work, specification, planning and loop workflows |
| `web/src/` | The screen; `web/src-tauri/` is the window around it |

The hooks (`tool/inject.py`, `tool/hook.py` and the other `PreToolUse` checks)
are separate from the program. They match the hub's rules against the
utterance without the search daemon or Jev, so a rule reaches the host
whatever retrieval or its audience scope does.

## Processes and state

| Process | Started by | State it owns |
| --- | --- | --- |
| The app server (`tool/main`) | `tool/app.cmd` or the Tauri window | Chat records in `raw/chat/`, runs, revisable specifications, task branches and legacy worktrees |
| The search daemon (`tool/search/daemon.py`) | The first search that needs it; replaced when its code hash differs | The evidence store and vectors in the user cache, per hub and project |
| Host sessions (Claude, Codex) | The app's work and review cells | Their own transcripts; the program reads their events |
| Mobile tunnel (`cloudflared`) | Explicitly enabled in desktop Settings | Temporary HTTPS hostname; paired browser requests reach the same app server |
| Hooks | The host, per event | The target's `.wiki/trajectory.jsonl` and hook diagnostics |

Indexes are derived data: a changed source makes a new store generation, and
one answer reads one generation. Documents and memories stay authoritative.

The [mobile companion](mobile.md) keeps execution on the desktop. Its paired
browser uses the same HTTP routes through a WebSocket bridge; project and
approval guards remain at those routes. The Python listener stays on localhost,
and only the desktop can create pairing links or revoke devices.

## The four graphs

Four different things are called a graph here. They do not stand in for each
other.

| Graph | Built by | Nodes and edges | Used for |
| --- | --- | --- | --- |
| Policy graph | `tool/graph.py`, into the root `graph.json` | Hub rules, their enforcement layer, rules injected together | The wiki map; co-injection edges feed retrieval |
| Repository document map | `tool/repo_graph.py`, into a target's `.wiki/graph.json` | A repository's documents and the paths they point at | The repository map and orphan checks |
| Evidence knowledge graph | `tool/search/knowledge_graph.py`, in the evidence store | Chunks, sources, entities, decisions; links, mentions, supersession | Retrieval's bounded graph lane; checked by `tool/relations.py check` |
| Workflow transition graph | Code: `Flow` in `tool/main/knowledge.py`, the loop in `tool/main/loop.py` | States of a question run or a review loop, and the allowed moves | Which function runs next; Jev chooses among moves code offers |

The knowledge graph finds evidence; the transition graph decides what runs.
A relationship in the knowledge graph is not proof that its content is true.

## Retrieval scope

A request carries its repository (`repo_id`), a source allowlist and optional
filters. Repository identity and `visibility` are access: a chunk from another
repository is never loaded, and a link into one is refused on the walk.

`filters.audiences` is relevance, not access. Given one or more of `product`,
`hooks`, `jev`, retrieval leaves out hub documents written for another
audience. The map is `HUB_AUDIENCES` in `tool/search/sources.py`, derived from
the path when the index loads, so no chunk id or indexed text changes:

- `operator/` and `craft/` answer every audience.
- Jev maintenance, its plans and its research answer `jev` and `product`.
- Hook and chat setup answer `hooks`.
- Other `docs/` and `.wiki/` pages answer `product`.
- Anything else, and every target repository's document, is unclassified:
  it stays eligible, and the result counts it (`audiences.unclassified`).

The filter applies before ranking and to every node the graph walk reaches.
A request without it searches as before; the screen's scope selector sends
none for "모든 문서". Hub maintenance pages are not ingested into other
projects: a connected repository sees the hub's shared rules only.

## Final gate

A repair round runs the checks its changed paths map to; the whole gate runs
once, on the commit review allowed, and merge is bound to that head. The flow
and its commands are in [development](development.md) and the
[reliability plan](plans/reliability/2-tests.md).

For Claude Code Cloud implementations, `tool/main/verification.py` adds
repository-specific local execution evidence before the independent review.
Failures return to cloud instead of opening a local implementation turn.
Configuration, receipts and the GitHub required status are described in
[local verification](local-verification.md). The local implementation path
keeps its existing review and final gate.
