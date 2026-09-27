# Stage 9 — product integration and observable decisions

See the [overall design](0-overview.md). Prerequisite:
[stage 8](8-agent-decisions.md).

Purpose. Users can tell whether Jev is connected, what it decided, which evidence
was used, what remains unknown, and whether a fallback occurred. App and CLI
must expose the same underlying run rather than separate implementations.

## Settings and connection state

Extend the existing settings modal with provider connectivity, responding model,
off/shadow/active mode, enabled sources, and configured run limits. Show key
presence and configuration source without exposing the value. The UI does not
write keys into browser storage or logs.

Shadow mode records Jev decisions but executes baseline behavior. Active mode
executes the controller. A configuration change applies to new runs; an in-flight
run retains its settings snapshot. A direct CLI invocation displays its effective
mode and override source, avoiding disagreement with the app.

## Run events

Each event includes repo_id, run_id, sequence, stage, status, elapsed_ms, and
budget_remaining. Specialized payloads reference evidence IDs, graph paths,
candidate IDs, or claim verification results. Failures include a stable reason
code and a readable message without provider credentials or raw headers.

Persist an append-only trace under the hub's scoped runtime directory. Record
atomic final summaries and use sequence numbers for reconnection. Redact secrets
and avoid duplicating full documents on every event. Persist source hashes and
locators; optional replay snapshots have explicit retention and access scope.

Code generates explanations such as source unavailable, evidence missing, or
threshold not met from observed state. It must not manufacture a chain of
thought for Jev, which supplies typed evaluations rather than reasoning prose.

## Answer presentation

The question pane shows progress through retrieval, graph expansion, checking,
and answer publication. Evidence entries open their original locations and
identify source kind, version, English representation, and support status.

Display complete, partial, abstained, verification unavailable, and cancelled
outcomes distinctly. A fallback is visible even when baseline search returned
results. A sufficiency estimate is not labeled as factual correctness.

The simplified explanation preserves citations and uncertainty from stage 7.
The UI never appends internal drafts to the visible assistant message while
waiting for verification.

Stage 7 leaves: the CLI has no grounded-answer path — `tool/eval/answers.py`
drives `knowledge.grounded` for the live sample only. The answer's status line
and checked overlay were seen in a window; the `표시 오류` fallback was seen
only while its causes were being fixed, and the plain overlay (`useOverlay`)
still sends a whole answer as one string, whose Korean silently fails when the
translator splits its paragraphs.

## Graph view

Reuse `RepoMap` and the current engine. Add a selected-run projection showing
seed chunks, traversed relationships, bridge evidence, accepted citations, and
discarded candidate counts. Preserve access to the original repository map.

Clicking an edge shows its type, direction, origin, and supporting source span.
Observed co-injection and extracted semantics are visually distinguishable.
The map is read-only and does not regenerate files in the original checkout.

## App and CLI interfaces

Proposed public routes:

| Route | Responsibility |
| --- | --- |
| `GET /api/knowledge/status` | Scoped source/index generation and provider status |
| `GET /api/knowledge/runs/{id}` | Owned run summary and trace references |
| `GET /api/knowledge/runs/{id}/events` | Ordered events with resume cursor |
| `POST /api/knowledge/runs/{id}/cancel` | Cancel through the run owner |
| Existing settings route or a narrowly scoped extension | Mode, enabled sources, and budgets |

Preserve the existing local-origin and selected-project checks. Knowing a run ID
is not enough to read another project's evidence. CLI structured output uses
the same schemas and can export a redacted trace without copying private source
content by default.

## Implementation locations

Use `main/knowledge.py` for run ownership and API composition, `main/query.py`
for question integration, and the existing settings route for configuration where
possible. Extend `web/src/lib/api.ts`, `Query.tsx`, `Settings.tsx`, `RepoMap.tsx`,
and `Stream.tsx` only as required by these flows.

At implementation time, apply the repository's design skill to the new UI.
The plan defines required information and behavior, not an unverified final
layout. Retain keyboard access, focus handling, readable status text, and
non-color-only distinctions.

## Completion gate

Verify a real window run for success, partial evidence, empty evidence, provider
outage, cancellation, reload, project switch, and removed source. Confirm that
an old run cannot overwrite the newly selected project or resurrect a cleared
conversation.

The displayed graph path must match the recorded traversal, and citation clicks
must open the actual source revision or explain why only a snapshot remains.
Browser payloads and exported traces must contain no key. App and CLI results
for one frozen input must have matching decision and evidence identities.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Settings | Connection, mode, sources, limits | Not started |
| 2 | Events | Durable trace, resume cursor, cancellation | Not started |
| 3 | Evidence | Verified answer states and citation inspection | Not started |
| 4 | Map | Actual traversal paths and relation provenance | Not started |
| 5 | Verification | Window, CLI, isolation, accessibility, recovery | Not started |
