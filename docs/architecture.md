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
| `web/src/` | The screen; `web/src-tauri/` is the desktop window around it |
| `android/` | The APK shell: QR pairing, saved HTTPS origin and hardened WebView |

The hooks (`tool/inject.py`, `tool/hook.py` and the other `PreToolUse` checks)
are separate from the program. They match the hub's rules against the
utterance without the search daemon or Jev, so a rule reaches the host
whatever retrieval or its audience scope does.

[Self-improvement](self-improvement.md) has two separately owned scopes: the
hub's harness and each connected repository's code and workflows. The explicit
`tool/improve.py` runner compares candidate patches under a frozen evaluator,
records their performance and cost, and prepares a local review branch after
held-out validation. Operating records are namespaced by scope and canonical
Git identity; project results never become shared rules automatically. Normal
Stop hooks and conversations do not start experiments.

Native hooks own SessionStart context, UserPromptSubmit injection, PreToolUse
checks and Stop reconciliation. `host_boundary.py` redirects accidental legacy
desktop commands to the application's workflow. Reinstall removes the old
desktop hook bridge and this hub's retired keepalive commands while preserving
unrelated hooks. The search daemon has no terminal transport or idle model turns.
Managed sessions strip inherited desktop transport variables. Claude's automatic
skill loading is disabled; Codex disables inherited skills whose instructions
reference the retired desktop host using process-local `skills.config` overrides.
The selected CLI login and unrelated Codex skills stay in their current home.

PR publication, including planner handoff, stops before review. The person
explicitly starts Review Loop once per PR; only that authorized loop resumes
automatically after interruption. A task PR published manually may precede
`done-report`. The review-loop button
matches that PR by exact task branch, verifies repository and checkout ownership,
and attaches it to the existing specification through server-owned metadata.
Requirements, revision, history and task identity survive. Busy or wrong-branch
checkouts are refused before metadata changes; review still checks the real PR
head and runs the repository gate.

Observed causes, regression scope and real host-event evidence are recorded in
[native host boundary and PR recovery](research/native-host-boundary.md).

## Processes and state

| Process | Started by | State it owns |
| --- | --- | --- |
| The app server (`tool/main`) | `tool/app.cmd` or the Tauri window | Chat records in `raw/chat/`, runs, revisable specifications, task branches and legacy worktrees |
| The search daemon (`tool/search/daemon.py`) | The first search that needs it; replaced when its code hash differs | The evidence store and vectors in the user cache, per hub and project |
| Host sessions (Claude, Codex) | The app's work and review cells | Their own transcripts; the program reads their events |
| Mobile tunnel (`cloudflared`) | Explicitly enabled in desktop Settings | Temporary HTTPS hostname; paired browser requests reach the same app server |
| Hooks | The host, per event | The target's `.wiki/trajectory.jsonl` and hook diagnostics |

One app server owns a hub's persisted workflows. Startup holds an OS lock on
`raw/server.lock` before recovering interrupted tasks; a second server cannot
mark the first server's live review as stopped. Startup resumes active loops
and previous restart stops after recovery; explicit user stops and blockers
remain stopped. The poller reattaches active states without a driver. Shutdown
gates new driver creation, stops watchers and review drivers, and drains work
callbacks and planning handoffs before releasing ownership. Accepted late
review handoffs stay queued for the next owner's startup recovery.
Review correction stays in its current round until the work session returns a
complete finding report. A report without an id must uniquely identify one
finding, including when distinct findings share a file and line. Claimed fixes
need a new commit, and round checks and publication precede the next review.
Evidence and regression scope are in
[task lifecycle recovery](research/task-lifecycle-recovery.md).

The Suite tab reads scoped work, planning, review and conversation records and
their live runs through `GET /api/suite`. It shows active cells and the latest
100 historical executions, including failures and unanswered approvals. It
does not dispatch work; server-owned completion callbacks advance tasks even
when transcript or feed publication fails.

Claude background execution stays within the work turn until task lifecycle
events settle and the provider's follow-up result arrives. `_drain` forwards
task start/progress/update/notification, elapsed tool progress and bounded raw
tool output to the same Agent stream and history. A foreground result is
intermediate while any task still owes a follow-up, even if execution already
ended. Result `origin` distinguishes human and task-notification responses;
empty batched results do not close the turn. `task_updated.patch.status` can
settle execution even without `task_notification`. Child text never replaces the parent
answer. Raw tool output remains outside automatic translation.
Older unattributed results use replayed human/synthetic boundaries and count
each task's follow-up independently. A failed result closes its provider session
before another prompt can reuse the channel, so late frames cannot answer a new
request. The [lifecycle and publication research](research/background-lifecycle-architecture-refresh.md)
records the review reproductions and protocol evidence.
Codex command output deltas and MCP progress notifications also reach the
same stream; command output retains its raw-display marker in history.

Indexes are derived data: a changed source makes a new store generation, and
one answer reads one generation. Documents and memories stay authoritative.

The [mobile companion](mobile.md) keeps execution on the desktop. Its paired
browser or Android APK uses the same HTTP routes through a WebSocket bridge;
project and approval guards remain at those routes. The APK adds no execution
API or JavaScript bridge: it validates and opens the one-use HTTPS pairing link.
The Python listener stays on localhost, and only the desktop can create pairing
links or revoke devices.

The shared change feed also invalidates conversations and work records/state.
Both PC HTTP and paired WebSocket clients subscribe, refresh their scoped
records and follow live runs started by the other client. Feed reconnect uses
the last accepted sequence and a fresh snapshot; it never resubmits writes.
The native APK needs no execution or synchronization bridge for this behavior.

## The four graphs

The App structure tab is a separate source architecture view, backed by
local oh-my-mermaid documents in the selected repository's `.omm/`.
The server's `main/architecture.py` reads documents without generating them.
Explicit creation and `tool/omm_scan.py` use a native model CLI with read-only
source tools to trace callers, routes, state and persistence, then validate
and write architecture fields through `omm`. Diagrams have meaningful labeled
connections and described child elements. Existing maintainer context and
constraints survive generation. Frontend builds never generate architecture.
Clicking Merge runs `main/maintenance.py` on the PR checkout before merge:
wiki lint repairs, document indexes and existing `.omm` updates are committed
and pushed together. A changed head receives review and its final gate before
that clicked request continues. The request is bound to the prepared head,
PR, base and specification revision. A later repair cannot inherit it.
Generation never runs on the base after merge. The frontend polls the saved
documents every five seconds. A repository without `.omm`
offers explicit creation bound to the selected project request guard.

The [merge and translation investigation](research/merge-authorization-maintenance.md)
records the runtime evidence, defects and regression coverage.

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
