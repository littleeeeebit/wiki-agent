# Answer path — verification after #59

Written 2026-10-10 at 5992829, from the v4 and v5 calibration answer runs.
A design to decide, not an implemented change. It answers the open question
v5 left for its answer gates (`5-english-baseline.md`, "Exit on v5").

## The problem

#59 (2026-10-01) made ordinary conversation synthesize: the host writes the
answer, it is recorded `unverified`, and the claim validator runs only when a
caller passes `verify_claims=True`. No production caller does
(`tool/main/query.py:808`, `tool/jev_search.py:103`); only tests do. The
evaluation's arm D follows the product path, so since #59 it measures
synthesis over Jev's retrieval against synthesis over the plain brief (A).

The same 72 calibration intents, by question class. v4 ran at 02fab78,
before #59; v5 at 21dff77, graded against what the host read (#109):

| Run | Arm | Class | Rows | Claims | Unsupported | Rate | Abstained |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4 | D | fact | 57 | 132 | 7 | 0.053 | 5 |
| v5 | D | fact | 57 | 401 | 85 | 0.212 | 1 |
| v5 | A | fact | 57 | 403 | 88 | 0.218 | 0 |
| v4 | D | analysis | 3 | 41 | 13 | 0.317 | 0 |
| v5 | D | analysis | 3 | 45 | 15 | 0.333 | 0 |
| v5 | A | analysis | 3 | 37 | 0 | 0 | 0 |
| v4 | D | abstain | 6 | 6 | 0 | 0 | 6 |
| v5 | D | abstain | 6 | 50 | 8 | 0.160 | 6 |
| v5 | A | abstain | 6 | 60 | 8 | 0.133 | 6 |

What it shows:
- Fact questions are 57 of 66 rows and carry the whole difference. With the
  validator, D published a third as many fact claims and a quarter of the
  unsupported rate; without it, D is A.
- Analysis answers were synthesized in both runs (`Grounding.analysis` is
  true for an analysis dossier even with `verify_claims`), and D's rate did
  not move. A's was 0 on the same three rows. This design leaves them alone.
- The validator's cost was abstention: 5 of 57 fact questions were recorded
  `abstained` and left their question unanswered (`direct-01`, `bridge-07`,
  `bridge-10`, `bridge-11`, `fix-12`). Four of the five still published
  accepted claims; `bridge-11` states that exports use the cold bucket but
  not which cipher secures them. The validator abstains when no requested part
  is answered by a shown claim (`Grounding.published` in `knowledge.py`),
  not only when every claim is rejected. On the abstain intents D now adds 50
  claims around a correct "not found".

Two measurement caveats:
- Arm A has always had Read, Glob and Grep (`compare.answered`), and before
  #109 the grader never saw what A read. v4's A rate (0.42 on facts) is
  inflated by that, so v1–v4's answer support overstated D's advantage. On v5
  the fix moved A from 0.307 to 0.192 overall.
- Calibration is 57 fact rows; every rate above is a diagnostic, not a gate.

## What #59 fixed, which must stay fixed

From the #59 record (`.wiki/decisions/2026-10-01-059-fix-wiki-answer-synthesis.md`):
1. Retrieval uncertainty, an undecided judgment or a spent call allowance
   never refuses the whole answer.
2. The body carries no inline evidence marks or verification notices; sources
   and status live in "View evidence and judgment".
3. Progress questions (next task, wiki, retrospect) interpret repository
   observations, which are not retrieved passages a validator can check.
4. A synthesized answer is never promoted to verified.

## Options

| Option | What changes | Expected effect | Cost and risk |
| --- | --- | --- | --- |
| A. Restore v4 | `verify_claims=True` for ordinary conversation | Fact rate near v4's 0.05 | Reintroduces whole-answer abstention (5/57), against constraint 1 |
| B. Verify facts, never abstain | Answers B's purpose rule (below) sends to verification go through the validator; accepted claims are rendered as plain prose under #59's presentation; when the outcome would be `abstained` or `verification_unavailable`, one synthesis turn over the same dossier is published as `unverified` instead | Fact rate between v4 and today; no fact question recorded `abstained` | Jev calls per verified answer (v4 added latency 5.48 s, inside the 10 s gate) and a second host turn on each fallback; the fallback reopens a path to unsupported claims, bounded to answers where no shown claim answered the question |
| C. Grounded drafting only | The drafting prompt asks for only what the passages state, each sentence citing an id; no Jev check | Unknown; cheapest | Self-reported grounding is what the validator exists to check |
| D. Keep synthesis, change the gate | Answer support becomes non-inferiority of D to A | Undetermined until a margin is chosen: on `reliability-v5-calibration-answers-2` D's unsupported rate is 0.218 (108/496) against A's 0.192 (96/500) overall, a loss of 0.026, and 0.212 against 0.218 on facts | Gives up the answer-side claim of Jev; needs a margin, a cohort (all rows or facts) and whether the margin binds the point estimate or the interval, in a new gate version, by the owner's decision |

B follows the post-hoc attribution pattern: draft freely, then check each
atomic claim against retrieved evidence and revise what fails, rather than
refusing up front (RARR, R1; atomic claim scoring, R2; citation evaluation,
R3). It keeps constraints 1–4: no whole refusal, nothing in the body,
progress and analysis answers synthesized as today, and only
validator-accepted claims are ever marked verified.

### Which answers B verifies

The fallback's trigger is the validator's own outcome, not an empty claim
list: four of v4's five abstentions had accepted claims that answered no
requested part. The fallback body cannot come from the verified draft,
which is structured claims under `answer-draft.md`; it is one more host turn
under `answer-analysis.md`, the path an analysis dossier takes today.

A dossier carries no purpose. A progress question that asks for a count or
a status routes as a fact question (`ANALYSIS` excludes counts), and
`Grounding.rebase` reads only `verify_claims` and the analysis flag.
Verified, it would be drafted under `answer-draft.md`, which cites only
retrieved evidence, so the repository observations #59 supplies
(`jev_search.repository_state`) could ground nothing: the boundary #59
removed. B therefore adds one purpose decision asked beside `ANALYSIS`, as a
Jev kind with its own digest: does the query ask about this repository's own
progress (what is done, in progress or next), which current repository
observations answer, rather than for facts its sources state? Only a sure no
is verified; yes, uncertain or an unavailable decision synthesizes as today.
It is one decision in `knowledge`, not a list of question expressions.

The policy lives in `grounded`, decided from the dossier, so every caller
gets it: ordinary conversation (`tool/main/query.py`, `tool/jev_search.py`)
and arm D (`tool/eval/compare.py`, which calls `grounded` itself). The
calibration then measures the production policy. An explicit
`verify_claims=True` keeps its current meaning for the callers that pass it.

## Recommendation

B, decided by a calibration experiment before any held-out spending:
1. Implement B: the purpose decision, the policy in `grounded`, and the
   synthesis fallback on an `abstained` or `verification_unavailable`
   outcome.
2. Run the calibration answers again (A and D, about USD 9) and compare with
   `reliability-v5-calibration-answers-2`.
3. Adopt when, on the 57 fact rows: D's unsupported rate is at most 0.10, no
   D row has `answer.status` `abstained`, D's grader-abstained rows are no
   more than A's, and D's coverage is within 0.05 of A's. Report how many
   fact rows the purpose decision sent to synthesis. Otherwise record the
   result and bring the options back to the owner.
4. Then the held-out answers (about USD 18) for answer support and added
   latency on v5.

Not decided here: whether analysis answers need any check (three rows, D
0.333 and A 0, are too few to act on), and how progress answers (constraint
3) should be evaluated at all. v5 has no progress intents, so the experiment
measures what the purpose decision costs fact answers, not whether it
protects progress answers.

## Calibration result at fa395e3

B as implemented on `feat/answer-path-b`, run as
`reliability-v5-calibration-answers-b` (A and D, English). The run stopped
at its USD 10 ceiling after 128 of 138 rows (44 minutes, USD 10.00): the
fact intents `fix-09` and `fix-12` and the injected-failure rows were not
reached, so the fact cohort is 55 of 57.

| Fact rows | A | D under B |
| --- | --- | --- |
| Rows | 55 | 55 |
| Claims | 350 | 175 |
| Unsupported | 59 (0.169) | 6 (0.034) |
| `answer.status` abstained | 0 | 0 |
| Grader abstained | 0 | 0 |
| Coverage | 0.991 | 0.982 |

Against the adoption rule, all four hold on the 55 rows: the unsupported
rate is under 0.10, nothing is recorded `abstained`, the grader found no
unanswered fact question in either arm, and coverage is 0.009 under A's.
The purpose decision said no on 54 of the 55 fact rows and uncertain on
`memory-08`, which was synthesized. Two fact rows fell back
(`bridge-10`, `bridge-11`, both v4 abstentions), as did five of the six
abstain intents; those five published no unsupported claim. Analysis rows
were synthesized as before (D 15 of 44 unsupported, A 7 of 30).

Latency is the cost. Added latency, D's answer p95 over A's, is 17.64 s
(D 33.30, A 15.66) against the gate's 10 s; it was 6.25 s for v4's
calibration and −3.37 s for `reliability-v5-calibration-answers-2`. Verified
single-turn rows have a median of 14.7 s against A's 8.8 s; 13 of 64 D rows
took a second host turn (6 repairs, median 31.0 s; 7 fallbacks, median
17.9 s). A's own p95 moved from 24.09 s to 15.66 s between the two v5 runs,
so the gap is partly the host's variance, but the held-out added-latency
gate is at risk.

## Sources

- R1: Gao et al., [RARR: Researching and Revising What Language Models Say, Using Language Models](https://arxiv.org/abs/2210.08726), 2022.
- R2: Min et al., [FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://arxiv.org/abs/2305.14251), 2023.
- R3: Gao et al., [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627), 2023.
