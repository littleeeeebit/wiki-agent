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
| 1 | Candidates | ActionProposal and operation registry | Done — `tool/main/decisions.py`: `ActionProposal` (`action-proposal/1`) with every field this plan names; the seven operations are a closed tuple and `candidate` refuses any other. Code builds each point's candidates; Jev reads their ids and descriptions and answers one `action` Choice (a new Choice kind in `decision/policy.py`, provisional confidence 0.6 and margin 0.2) with `defer` beside them, through the stage 6 `decision.request`/`decide`/`checked`. The prompts carry their own version and artifact (`eval/jev/action-policy.json`, not written yet), so retrieval's fitted rules stand. Registered checks come from the adapter's `[checks]` table (`name = "cmd"` or `{cmd, about}`, ids `[a-z0-9_-]`, at most eight, the gate's own command left out); a model names a check by id and never writes a command. Every choice is an `action-record/1` row in `raw/actions/<repo>/<spec>.jsonl` — offered, predicted, selected and rejected ids, Jev's answer, policy, budget — with outcome rows after it, so an action correlates with its spec by file and by `spec_id`. `/api/jev` lists each point's callable entry points, operations and baseline, and says that a host's own tool planning is outside controller coverage |
| 2 | Owners | Specification, work-turn, check, and review integrations | Done — `work.start`: `[시작]` starts its turn with `decide=True`; on the turn's thread (`work.run_turn`, so a stop reaches it) Jev chooses to send the turn, gather evidence first (`knowledge.prepare` on the spec's goal, attached to the turn with each passage's locator and chunk id, passages addressing the agent left out), or — only when code finds the spec has no acceptance criterion beyond the gate — ask the person and send nothing, with the question on the turn and the spec's `fault`. What was sent is what the record keeps. `specs.check`: after the gate passes, Jev may pick one registered check that examines the files the branch touched; it runs in the worktree, a failure holds the pull request, a pass goes into its body. `loop.fix`: at a refused round, send the findings, or gather the context they touch first (findings disputed the round before are named). `specs.answered`: a `candidates` block is reordered with Jev's pick first and marked `recommended`; nothing runs, the person chooses. `query` is stages 6 and 7's flow, listed, unchanged |
| 3 | Validation | Revision, permissions, idempotency, cancellation | Done — `decisions.admit`, right before the owner runs anything: the operation registered, the candidate still offered with the same arguments, the authorization the operation needs (`session_approval` for a work turn, `adapter_registration` for a check — neither granted here), the missing input a question needs, then repository, worktree, session, spec revision (edits and state moves), HEAD and expiry, and last the idempotency key (point, occasion, spec, revision, HEAD), claimed once per process. A stale proposal is asked again on the same budget; a second stale one is not applied — the baseline is proposed in its place without Jev and checked the same way. A duplicate delivery runs nothing. A cancel heard before, during or after the answer runs nothing, and the owner reads its stop again before it sends. `decisions.replay` decides a record again from its answer and policy and runs nothing |
| 4 | Recovery | Uncertainty, outage, and deterministic continuation | Done — uncertain, deferred, invalid, unavailable, exhausted and normalization failures each run the owner's baseline — what it did before this stage — and the record keeps which. Prose Jev reads is normalized to English first; text with no English is never sent, and the choice falls back. Mode off asks nothing; shadow records Jev's pick in a background thread beside the baseline and waits for none of it; only active runs the pick. The required gate, approvals, the review's verdict, the round cap and the merge conditions are untouched by every point: Jev can add a check or a turn's evidence, never remove one. The loop stops with its existing reasons if a correction is refused three times over (`NO_WORKTREE` when the worktree went); no new stop reason was added |
| 5 | Verification | Three live decision points and measured outcomes | Done — `tool/test_agent_decisions.py`, 24 cases with no network: the proposal's fields; unregistered operations, unknown and changed candidates and a lowered authorization refused at the boundary; an answer naming an unoffered id reaching no executor; defer, doubt, 503 and 401 running the baseline; a changed HEAD regenerated on one budget; state that keeps moving ending in the un-asked baseline; a cleared context, a removed worktree, a new revision and expiry invalidating; duplicate delivery; a cancel landing with the answer; Korean prose never sent; off and shadow; replay; two competing sources both attached and the redirect dropped; and through the app, `[시작]` with evidence, with a question and with a stop, a registered check passing into the PR body and a failing one holding the PR, and the review loop sending context or, on an outage, the plain correction. Live (`tool/eval/actions.py`, 2026-09-27, the smoke corpus, real Jev and retrieval, stand-in host sessions and GitHub, `raw/eval/jev/actions-20260927T101849Z.json`): every point changed the executed operation on at least one fixture — work.start gathered evidence (0.98) and asked for acceptance criteria (0.97) where it would have sent `Start.`; specs.check ran `docs-links` for a docs change (0.85) and `unit` for a code change (0.80) where it would have run nothing, and chose none for a change neither check covers (0.93); loop.fix gathered context for a finding resting on a recorded decision (0.93) and sent a local off-by-one as it was (0.98). Seven of eight accepted picks matched the operator's label; the eighth, a complete spec, was uncertain (dispatch 0.67) and ran the baseline, which was also the label. Every record replays to the same pick. Nothing Korean sent; 8 action requests, 4,556 input tokens. The first live run asked the check question as "evidence the gate did not give", which Jev cannot know: it leaned to none (0.56 and 0.63) while ranking the right check far above the wrong one; the question now asks which check examines the touched files |

Owned by later stages, deliberately: fitting the action policy and comparing decision quality with the existing behaviour on held-out fixtures before active rollout are [stage 10](10-evaluation-rollout.md)'s — eight live fixtures are a smoke check, not calibration. Showing the records, the recommendation and the checks on the screen is [stage 9](9-product-observability.md)'s. `read_registered_source` and `defer` are registered operations no point offers yet; `/api/jev` says so. The idempotency ledger lives in memory: a restart forgets it, and no loop resumes by itself after one. A check round runs at most one extra check a turn. The live run's host sessions are stand-ins: it measures the choice and the owner's execution, not a model's code.
