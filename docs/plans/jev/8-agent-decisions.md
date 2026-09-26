# Stage 8 — connect Jev to real agent choices

See the [overall design](0-overview.md). Prerequisite:
[stage 7](7-grounded-answer.md).

Purpose. Jev must influence actual choices beyond passage reranking. Integrate
with the specific decision points this application owns, and demonstrate that
the selected operation changes observable execution.

## Capability boundary

The app does not own every hidden decision inside a Claude or Codex CLI session.
Adding a prompt about Jev does not intercept that host's internal tool planner.
This stage covers server-owned turn dispatch, knowledge preparation, candidate
ordering, and transitions between bounded work/check operations.

The existing host remains responsible for code generation and its permitted tool
use. This stage does not spawn child agents or replace the host with a new agent
framework. Host-internal selection is explicitly reported as outside controller
coverage, rather than presented as implemented.

## ActionProposal contract

Fields: schema_version, proposal_id, repo_id, worktree_id, session_id, spec_id,
spec_revision, head_oid, operation, candidate_id, evidence_ids, preconditions,
authorization_required, expiry, and idempotency_key.

Operations form a code-owned enum: retrieve_evidence, read_registered_source,
request_clarification, prepare_work_turn, run_registered_check, summarize_result,
or defer. Jev chooses IDs from candidates assembled by code. It cannot return an
arbitrary command, new file path, tool name, or executable argument string.

## Integration points

| Owner | Choice controlled by Jev | Execution and limits |
| --- | --- | --- |
| `main/specs.py::materials/answered` | Rank supported next-work candidates; identify missing decision inputs | Existing candidate UI presents them; the user chooses goals |
| `main/work.py::begin/run_turn/dispatch` | Prepare evidence first, request missing input, or dispatch a bounded work turn | Existing session, worktree, queue, stop flag, and approval handling |
| `main/specs.py::check` | Select useful additional registered checks from evidence | Required adapter gate always runs; Jev cannot skip or weaken it |
| `main/loop.py` at a fixed review result | Retrieve context for a disputed finding or send the next existing correction turn | Deterministic review outcome, round limit, and merge conditions remain authoritative |
| `main/query.py` | Search, answer partially, or ask a targeted clarification | Stage 7 publication policy and existing user-choice UI |

After implementation, list exact callable entry points and supported operation
coverage in the product diagnostics. A recommendation that never affects any
dispatch path does not satisfy this stage.

## Execution protocol

1. Code builds candidates from the current state and registered capabilities.
2. Normalize only the information needed for this choice, using the evidence
   and state contracts from stage 2.
3. Jev returns a typed choice, probability distribution, confidence, and a
   possible defer result. Record selected and rejected candidate IDs.
4. Code rechecks repository, specification revision, HEAD, session ownership,
   expiry, required inputs, and authorization immediately before execution.
5. Execute through the existing owner function. Record actual outcome separately
   from the model's prediction or expected benefit.
6. An outdated proposal is invalidated and regenerated under the same budget;
   it is never silently applied to a new HEAD or user goal.

Operation identity makes a replay observational. Replaying a recorded decision
must not rerun its shell command or resend a work turn. Duplicate callbacks and
reconnection events cannot execute an action twice.

## User decisions and permissions

Selecting a feature goal, widening scope, approving writes, merging, deleting a
worktree, or raising a spending limit remains with the existing user flow.
Jev may recommend an option and explain its supporting evidence, but confidence
does not count as a submitted answer or permission.

Keep mandatory checks and authorization as code-owned prerequisites. A configured
bypass mode keeps its existing explicit semantics; this feature neither enables
it nor invents a second permission mechanism.

## Uncertainty and outage

For low-confidence choices, prefer an existing deterministic safe step when one
is clearly applicable, otherwise defer with a specific unresolved input.
Use a generative model to analyze an ambiguous choice only within the same
budget and capability boundary; it does not gain authority Jev lacked.

On provider failure, retain current work state and the existing queue. Do not
mark work completed, send a fabricated approval, or turn an error into a tool
selection. User cancellation prevents subsequent dispatch even if a late response
arrives successfully.

## Files and tests

Put proposal construction and validation in `tool/main/decisions.py`; reuse the
stage 6 decision API. Modify only the listed owner functions and the relevant
API types. Register concrete checks from adapter/task data rather than adding
a general-purpose shell execution service.

Fixtures include two competing evidence sources, missing acceptance criteria,
a changed HEAD, a cleared context, a removed worktree, duplicate delivery,
cancelled work, and an unavailable provider. Test invalid candidate IDs and
unregistered operations at the execution boundary.

## Completion gate

At least three distinct server-owned decision points use real Jev responses to
change the chosen operation in a recorded end-to-end run. Candidate-ranking-only
screens are insufficient. Every action has provenance, precondition checks,
outcome, and a way to correlate it with the user's specification.

Required gates, user approval, and merge matching remain enforceable even when
all model responses are malicious or wrong. Compare decision quality with the
existing behavior on stage 10 fixtures before active rollout.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Candidates | ActionProposal and operation registry | Not started |
| 2 | Owners | Specification, work-turn, check, and review integrations | Not started |
| 3 | Validation | Revision, permissions, idempotency, cancellation | Not started |
| 4 | Recovery | Uncertainty, outage, and deterministic continuation | Not started |
| 5 | Verification | Three live decision points and measured outcomes | Not started |
