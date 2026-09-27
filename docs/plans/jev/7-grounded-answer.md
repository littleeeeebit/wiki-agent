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
What code checks is a stand-in for meaning, so neither is presented as the
verified answer: the Korean is labelled a translation, the explanation an
unchecked rewrite, and the verified English stays one click away.

## Persistence and downstream consumers

Record drafts as internal run artifacts, never as successful assistant messages.
Conversation history, `specs.blocks()`, and `memory.keep()` consume only the
accepted answer and its verification metadata. Preserve unknown fields when
reading old logs, but never infer verified status from an old plain-text answer.

For specification cards, factual grounds use accepted evidence IDs and source
revisions. User choices remain user choices; a verified evidence record does not
approve a proposed implementation.

## Inherited from stage 6

Stage 6 leaves two decisions for this stage to enforce and fit. Both belong
here because their only consumer is the answer.

- A dossier with `direct` carries `restrictions`, which are instructions to the
  generator and nothing more. Verification rejects every `source_fact` claim in a
  direct run: direct mode cannot introduce a repository fact. A draft that needs
  such a claim goes back to retrieval, never through to publication.
- The `relation` Choice (supports, contradicts, insufficient) is still on
  `decision.policy`'s provisional confidence and margin. Label claim–evidence
  pairs in the calibration split and fit it with `tool/eval/policy.py`, as the
  repair Choice was fitted. False acceptance is costed above false rejection.

The dossier's `requirements` are the question's parts. Stage 6 split them, with
the model where one sentence asks several things; they are what
`question_requirements` and `unresolved_requirements` refer to.

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
| 1 | Draft | Structured claims, requirements, and evidence references | Done — `main/knowledge.py` `Grounding`: in active mode the focus's own session drafts over a brief (`tool/prompts/answer-draft.md`) carrying the English question, the requirements, the missing ones, known conflicts, untrusted passages, and the evidence under run-owned ids (`e1`…) with each passage's original and English text. It answers with one ```` ```answer-draft ```` block; code fills in `AnswerDraft` (`answer-draft/1`: schema_version, run_id, generation, question_requirements, claims, unresolved_requirements, proposed_status). A block that is missing, repeated, not JSON, with a field too many or too few, or with repeated or malformed claim ids is no draft. Claims are source_fact, inference (premises are earlier factual claims), recommendation (cites nothing; its facts are its premises) and direct_text (cites nothing, answers only a direct run's requirements) |
| 2 | Verification | Original-span checks, semantic support, a fitted relation policy, direct-mode enforcement | Done — code rejects before any judgment: a malformed claim, text_en that is not English, an unknown requirement or evidence id, a source_fact without a quote, a quote naming evidence the claim does not cite, shorter than eight characters, or absent from that evidence's `original_text` (the quote of another source does not count), a cited file whose lines no longer read as retrieved (`stale_revision`), a claim resting only on a passage flagged as addressing the assistant, and every source_fact of a direct run. Jev then judges each remaining factual claim with the relation Choice (`decision/claims.py`) — one request for all claims, each over only the passages it cites, an inference over its premises' passages as one set of at most six — and an inference stands only if every premise does. The relation prompt has its own version and artifact (`eval/jev/relation-policy.json`), so retrieval's fitted rules stand. A `direct_text` is published only in a direct run, and only when it states no number or identifier the question and conversation did not (review round 1: one labelled so in a retrieval run was published unchecked). A recommendation needs premises. Neither cites a passage, so the same request asks the faithful Choice of each (faithful, adds, contradicts): does it state a fact its grounds do not — a recommendation's grounds are its premises, a direct run's text the conversation — and it is published only on Jev's `faithful` (review round 2: `Acme owns the ingest pipeline` carried no number to catch and was published complete, and a recommendation with no premises passed an `all()` over nothing). The same request asks the answers Choice — does each claim give what each part of the question it names asks (answers, partly, no) — and a part counts as answered only on Jev's `answers`, never on the drafter's `requirement_ids` alone (review round 1: a true fact beside the point made an answer complete). Calibration (`eval/jev/relation.json`, `eval/jev/answers.json`, `eval/jev/faithful.json`, `tool/eval/policy.py --relation`): 20 requests, 31,274 input tokens. Relation, 39 pairs: false acceptance 0, false rejection 0, one contradiction labelled insufficient. Answers, 42 pairs: false acceptance 0, false rejection 6 — five on claims the relation rejects anyway, one (`kept password login`, answering how login was settled) where Jev's `partly` is defensible; four `partly`/`no` swaps. Faithful, 30 claims (five direct runs, five sets of recommendations): false acceptance 0 — every claim adding or contradicting a fact was chosen `adds` or `contradicts` — and false rejection 3, recommendations Jev called faithful below 0.6. The stage 7 fits only tighten: left free, answers fell to confidence 0.3, where a `no` chosen `answers` at 0.33 stayed out by its margin's last digit, and a few dozen synthetic pairs do not license a looser publishing rule. All three keep confidence 0.6 and margin 0.2 |
| 3 | Publication | Buffering, repair, partial answer, abstention | Done — `main/query.py`: in active mode no delta is sent; progress goes out as lines of what ran (`검증 · …`), and `done` carries only the text code renders from accepted claims — each with its citation, inference and recommendation labelled — plus conflicts, the count withheld, the requirements not established and a concrete next search. Status is complete, partial, abstained or verification_unavailable (`verified-answer/1`), from the requirements accepted claims answer, never from `proposed_status`. One repair at most, only for a reason a repair can mend (every code rejection and a contradiction, never an uncertain or unsupported claim), only while a Jev call is left to check it, and told the exact rejected claim ids and the evidence ids it may cite; a supported claim it keeps is not asked about again. A direct run's needed fact goes back to retrieval (`prepare(require=True)`) on what is left of the run's allowance, then to that repair. The Jev allowance is what retrieval left — calls and tokens — and each verification gets 20 seconds of its own, since the host's drafting between them is not Jev's time. An outage withholds every claim Jev could not check; `WIKI_JEV_DEGRADED=baseline` in `.env` publishes them as an unverified answer instead. A closed stream discards the draft and releases the focus |
| 4 | Consumers | Explanation, memory, and specification boundaries | Done — the draft is a `draft` row, never an assistant message; the assistant row holds the published text and its verification. A verified answer's plain explanation is sent only once checked, and refused as a presentation error when it states a number or identifier the answer does not (`translate.added`). Its Korean overlay goes through `/api/translate` with `checked`, a paragraph — one claim — at a time, which refuses a rendering that changed a number or identifier or that negates where its source does not, or the other way (`translate.checked`; review round 1: `not open` rendered `open` passed; presence refused 3 of 49 real paragraphs where a count refused 9, and the plain explanation, a free rewrite, is checked for numbers and identifiers only — negation held on 1 of 19 saved explanations), and the screen then shows the accepted original with a `표시 오류` line. Those checks cannot see a negation moved to the other clause or `denied` rendered `granted` (review round 2), so the screen calls the Korean a translation not checked for meaning, with the verified English one click away, and the plain explanation an unchecked rewrite; the translator is shown sixteen en→ko examples of exactly those turns (`tool/markers/examples.json`, part of en→ko's cache key; each passes the overlay's own check), and the explanation prompt two. A status line under the answer says how it was checked. `memory.keep` labels each answer `verified:<status>` or `unverified` — every older answer included — and the memory prompt marks a fact from an unverified answer as such. A verified `next` answer's spec cards keep only grounds an accepted claim cited, with `grounds.evidence` holding each chunk id and source revision; the card is still a proposal a person approves |
| 5 | Verification gate | Adversarial, bilingual, and live answer checks | Done — `tool/test_grounded_answer.py`, 46 cases with no network: fabricated quotes, another source's quote, unknown ids, stale revisions, copied-but-irrelevant quotes, missing premises, contradictions, incomplete multi-part answers, passages addressing the assistant, uncertain support, Korean evidence quoted in Korean and judged in English, direct mode and its return to retrieval, outages under both policies, an exhausted allowance, cancellation, an unreadable draft; and through the app, that rejected text reaches no SSE event, no conversation row, no memory and no spec ground. Live sample (`tool/eval/answers.py`, the smoke corpus, an isolated host session): smoke-01, the Korean smoke-02, the bridge smoke-03, the superseded-decision smoke-04 and the greeting smoke-09 complete, the unanswerable smoke-08 abstained; nothing Korean sent to Jev; 4 verification requests, 4,029 input tokens. The first live run exposed drafts attributing facts to file names Jev never reads (smoke-04 then abstained); the prompt now says to state the fact, and smoke-04's claims that still name a year only its file name holds are withheld. In the window (2026-09-27, `python tool/main`, this repository, a Korean question each): shadow streamed the baseline answer and recorded Jev's dossier beside it; active showed only progress lines until publication, then a complete answer with its three claims cited, a partial one listing the part no evidence settled, and their status lines, Korean overlays and plain explanations. The window found three faults the tests had not: a direct route whose draft, as told, left the needed fact unresolved abstained without searching — it now goes back to retrieval as a stated fact does; the checked overlay sent a multi-paragraph answer as one string, which the translator answered as several, so no Korean was shown — it now sends paragraphs; and `eight` rendered `8` was refused as a changed number, in the overlay and the plain explanation both — a presentation may now write a number the answer spells out, while evidence normalization still keeps its digits |

Owned by later stages, deliberately: a CLI path to a grounded answer does not exist — [stage 9](9-product-observability.md#answer-presentation)'s. The relation fit rests on 39 synthetic pairs where Jev was never close to the threshold; relation labels over the 60 calibration intents and a held-out measure of false acceptance are [stage 10](10-evaluation-rollout.md#evaluation-data)'s. Whether the generator follows the draft prompt is measured only by the six live answers: a claim that names a date or a file only the passage's metadata holds is still withheld, which fails safe but costs completeness.
