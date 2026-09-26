# Stage 7 — claims, citations, and controlled publication

See the [overall design](0-overview.md). Prerequisite:
[stage 6](6-decision-controller.md).

Purpose. Make evidence sufficiency affect what the user actually receives.
Unsupported generated claims must not bypass the controller through ordinary
streaming, a simplified answer, a specification, or saved memory.

## Structured answer contract

`AnswerDraft` contains schema_version, run_id, generation, question_requirements,
claims, unresolved_requirements, and proposed_status.

Each claim has claim_id, text_en, kind, evidence_ids, source_quotes, and
requirement_ids. Kinds are source_fact, inference, recommendation, or direct_text.
An inference lists its supporting premises and is labeled as an inference.
Recommendations may express judgment but their factual premises require support.

`VerifiedAnswer` contains only accepted claims plus explicit uncertainty,
conflicts, missing requirements, citation locators, verification version, and
status: complete, partial, abstained, or verification_unavailable.
These labels describe the verification performed, not universal truth.

## Generation and validation sequence

1. Main sends the generator the English question, allowed evidence IDs, known
   conflicts, and missing requirements. Evidence is untrusted data.
2. The generator returns a strict draft schema. Unknown evidence IDs and broken
   source revisions fail validation before semantic judgment.
3. Code verifies each quoted span against the pinned original snapshot. A real
   quotation must belong to the cited source and location, not merely occur in
   some other source.
4. Jev judges whether the associated evidence supports or contradicts each
   factual claim. Evidence needing multiple premises is evaluated as a bounded
   set; isolated partial facts do not certify the combined inference.
5. Compare accepted claims with all question requirements. This prevents a
   perfectly cited answer to only one part being labeled complete.
6. Publish accepted claims with their limitations, or use the repair path.

Reuse the [TypeSafe citation pattern](https://docs.typesafe.ai/cookbooks/citation_check):
deterministic span checks precede semantic support judgments. Its demonstration
results do not establish this application's accuracy.

## Failure and repair policy

| Condition | Required behavior |
| --- | --- |
| Fabricated quote or unknown evidence ID | Reject that claim; permit one constrained repair |
| Source contradicts the claim | Remove or rewrite the claim to report the conflict |
| Support uncertain | Keep unresolved; do not publish as established fact |
| Some requirements supported | Publish a partial answer and list the missing requirements |
| No usable evidence | Explicit abstention with a concrete next search or clarification |
| Verification provider unavailable | Show verification unavailable; baseline answer only under the separately configured degraded policy |
| Deadline or repair budget exhausted | Publish accepted material as partial, or abstain |

One constrained generation repair is the initial maximum. It receives the exact
rejected claim IDs and evidence restrictions. The original run budget is not
reset. Repeatedly regenerating until one judge approves is prohibited.

The default active policy withholds unsupported factual claims even during an
outage. A user-selected baseline mode may generate an unverified answer, clearly
identified as such, without a verified badge or promotion into verified memory.

## Streaming and UI contract

`main/query.py` currently forwards generator deltas. Active verification buffers
the draft and emits progress events while it is being checked. Publish only the
validated answer segments. Cancellation can discard the unpublished draft and
must release the conversation hold.

Do not send an unverified paragraph and later retract it: it may already have
been read, copied, or captured in another component. A normal final SSE `done`
event is emitted only after publication policy completes.

The Korean overlay and simplified explanation use the accepted answer as input.
They must preserve identifiers, numbers, negation, uncertainty, and citation
mapping. If transformation validation fails, show the accepted original answer
with a presentation-error indicator. Translation failure cannot create a new fact.

## Persistence and downstream consumers

Record drafts as internal run artifacts, never as successful assistant messages.
Conversation history, `specs.blocks()`, and `memory.keep()` consume only the
accepted answer and its verification metadata. Preserve unknown fields when
reading old logs, but never infer verified status from an old plain-text answer.

For specification cards, factual grounds use accepted evidence IDs and source
revisions. User choices remain user choices; a verified evidence record does not
approve a proposed implementation.

## Files

- `tool/main/knowledge.py`: generation/validation orchestration and publication policy.
- `tool/main/query.py`: buffered draft, SSE progress, accepted answer persistence.
- `tool/agent/`: schema-constrained generation through existing host capabilities.
- `tool/decision/`: source-to-claim Choice evaluation.
- `tool/main/memory.py` and `specs.py`: accepted-content boundaries.
- `tool/test_grounded_answer.py`: publication and downstream regression checks.

## Completion gate

Fabricated citations, copied-but-irrelevant quotes, missing premises, contradictory
documents, incomplete multi-part answers, and adversarial passage instructions
must have explicit expected outcomes. A test must prove that rejected content
never appears in SSE answer deltas, final history, memory, or specification grounds.
Live samples must include both a correct complete answer and an appropriate
partial/abstained answer. Measure false acceptance separately from false rejection.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Draft | Structured claims, requirements, and evidence references | Not started |
| 2 | Verification | Original-span checks and semantic support | Not started |
| 3 | Publication | Buffering, repair, partial answer, abstention | Not started |
| 4 | Consumers | Explanation, memory, and specification boundaries | Not started |
| 5 | Verification gate | Adversarial, bilingual, and live answer checks | Not started |
