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
| 1 | Settings | Connection, mode, sources, limits | Done — `POST /api/jev/settings` (`decision.save`) writes `raw/jev/settings.json` beside the hub's `.env`: a mode (or none, to follow the file), switched-off source families, and a question's seconds, calls and candidates within `decision.LIMITS`; the key is not among them. `decision.config()` reads it over the file and says where the mode came from (`mode_source`: app, file, environment, legacy, default); an unreadable settings file turns Jev off with `unreadable_settings`. A run takes a frozen `Config` when it starts, so a change applies to the next question. Settings → 질문 (Jev): health, model, key presence and source (never the key), 연결 시험 (the live probe), mode radios, source checkboxes, limits, and the sentence that a running question keeps its settings. The CLI prints the effective mode and its source to stderr first |
| 2 | Events | Durable trace, resume cursor, cancellation | Done — a question is a `knowledge.Run` on its own thread, not the HTTP body's: a reload or a closed stream leaves it running. Every event carries `run_id`, `seq`, `stage`, `status`, `elapsed_ms` and `budget_remaining`; `step` events come from the Flow's `emit` hook (the dossier and its replay are untouched) and from drafting, verification, repair and publication. The trace is `raw/knowledge/<repo_id>/runs/<run_id>.jsonl` beside the `.env` — streamed pieces left out, the key redacted — and `<run_id>.json` its `run-summary/1`, the newest 200 kept. `GET /api/knowledge/status` (settings, sources, generation, this project's live runs), `GET /api/knowledge/runs/{id}`, `GET …/events?after=` (tailed while live, read from the trace after a restart, `interrupted` for a run the server went down with) and `POST …/cancel`, which stops retrieval, verification and the host turn through the run's one event; a cancelled run publishes nothing. A run's trace starts with its `started` step, written where it is constructed, so a server that goes down before the first real step still leaves a run to call interrupted. Publication is one point of no return (`Run.seal`, under the same lock as the stop): a stop before it publishes nothing, a stop after it only cuts the plain explanation, and the cancel answers `published` to say which. The key is replaced in every event as it is made — the copies a screen tails, not only the trace — and in the summary Run ids are 32 hex characters and read only under the selected project: another project's id is a 404 |
| 3 | Evidence | Verified answer states and citation inspection | Done — the answer shows a stepper (검색 → 그래프 확장 → 검증 → 게시, marks and words besides colour), the stage as a sentence and 멈춤; a reloaded screen reattaches to its focus's live run from `seq` −1. 근거와 판단 보기 reads the run summary: outcome in words (partial, abstained, verification unavailable, cancelled, fallback to baseline), each note code in Korean (`lib/run.ts`), every evidence item with its support (✓ supported, ? unverified, ≠ conflict, ! untrusted, · not cited), cite button, kind, lane, revision and the English Jev judged. Reading a run checks each cited file against the revision the run read (`knowledge.standing`): a changed or deleted file says only the run's snapshot remains, and a deleted one is not offered as a link. The stage 7 leftovers: `jev_search.py --answer` runs the app's grounded path in a `Run` (`--run`, `--export`, `--with-text`), and every whole-answer overlay (plain answers, the agent pane, the map preview) is sent a fence-aware paragraph at a time (`paragraphs` in `lib/overlay.ts`) |
| 4 | Map | Actual traversal paths and relation provenance | Done — expand steps record the paths retrieval walked, and the summary's `graph.paths` are exactly those; `graph_detail` adds each node's label and each edge's kind, direction, origin, confidence and source spans from the graph as it is now. 지도에서 경로 보기 opens the map on an `이번 질문 경로` layer drawing only those nodes and relations (seed large, bridge dashed, cited evidence outlined, said in a legend), beside an ordered, keyboard-reachable list of every path; a node's panel lists each walked relation with its direction, origin, confidence and spans. The other layers stay one click away, and another project's map never draws the run |
| 5 | Verification | Window, CLI, isolation, accessibility, recovery | Done — `tool/test_observability.py`, 21 cases with no network: settings precedence, refusal and snapshot; limits and switched-off sources reaching retrieval; numbered events and a trace without pieces or the key; the recorded walk equal to the summary's paths; the app's steps, evidence support and summary; resume after a cursor and after a restart; an interrupted run; cancel; another project's run and a traversal-shaped id refused; export without texts; a changed and a removed cited file; and the app and `jev_search.answer` publishing the same outcome, evidence, claims and citations for one frozen input. In the window (2026-09-27, an isolated worktree server over two throwaway repositories, Jev active from a scratch `.env`): a partial answer, a complete one, an abstained empty one, a cancelled one (멈춤 during verification: nothing published, the row marked cancelled), a provider outage (no key: 검증 불가 · 기본 검색으로 대신함), a reload mid-run that reattached with its steps, a server restart that still read the summary, and a removed source no longer retrieved; a project switch or a clear from another window while a run was live was refused (409), and the screen keeps runs by project and focus. The drawn paths, the side list and the trace's expand events matched (11 paths, 9 relations); no key in 22 trace files or 18 payloads; the CLI export held ids and paths without a question or source text. The window found two faults the tests had not: the evidence was read before the run had finished its plain explanation (it now waits for `done`), and a retrieval cap was worded as a source being down (now `truncated`). A second pass the same day covered what the first had left: a live `--answer` from the command line, whose run the app then read and whose outcome, evidence and citations matched the app's for the same question (claims vary between drafts); a cited file edited and another deleted after their runs, noted in the disclosure and in the answer's own inline citations; and the screen at 400px inside a 400px frame (the window would not resize). That pass found four more faults: the CLI summary had no answer text (`answered` is now passed on); an inline citation opened a changed file without saying so, and a deleted one showed only `…` (the peek now carries the run's note and the cited place); long cites pushed the disclosure 39px sideways (they now break); and on a map pane 181px wide the path list and a document's panel were held to 70% of it, a character a line (they now take the whole pane). The shell's own header overlaps at 400px — loop stage 6's layout, not this stage's |
