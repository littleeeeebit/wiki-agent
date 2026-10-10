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
| v4 | D | abstain | 6 | 6 | 0 | 0 | 6 |
| v5 | D | abstain | 6 | 50 | 8 | 0.160 | 6 |

What it shows:
- Fact questions are 57 of 66 rows and carry the whole difference. With the
  validator, D published a third as many fact claims and a quarter of the
  unsupported rate; without it, D is A.
- Analysis answers were synthesized in both runs (`Grounding.analysis` is
  true for an analysis dossier even with `verify_claims`), and their rate did
  not move. This design leaves them alone.
- The validator's cost was abstention: 5 of 57 fact questions answered with
  nothing, and on the abstain intents D now adds 50 claims around a correct
  "not found".

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
| B. Verify facts, never abstain | Fact-routed answers go through the validator; accepted claims are rendered as plain prose under #59's presentation; when no claim survives, the synthesis is published as `unverified` with its stated limit instead of abstaining | Fact rate between v4 and today; no new refusals | Jev calls per fact answer (v4 added latency 5.48 s, inside the 10 s gate); the fallback reopens a path to unsupported claims, bounded to answers where nothing verified |
| C. Grounded drafting only | The drafting prompt asks for only what the passages state, each sentence citing an id; no Jev check | Unknown; cheapest | Self-reported grounding is what the validator exists to check |
| D. Keep synthesis, change the gate | Answer support becomes non-inferiority of D to A | Passes today's behavior | Gives up the answer-side claim of Jev; a gate change needs a new gate version and the owner's decision |

B follows the post-hoc attribution pattern: draft freely, then check each
atomic claim against retrieved evidence and revise what fails, rather than
refusing up front (RARR, R1; atomic claim scoring, R2; citation evaluation,
R3). It keeps constraints 1–4: no whole refusal, nothing in the body,
progress and analysis answers untouched, and only validator-accepted claims
are ever marked verified.

## Recommendation

B, decided by a calibration experiment before any held-out spending:
1. Implement B behind the existing switch: ordinary conversation passes
   `verify_claims=True` only for fact-routed dossiers, and the no-claim case
   publishes the synthesis as `unverified` instead of abstaining.
2. Run the calibration answers again (A and D, about USD 9) and compare with
   `reliability-v5-calibration-answers-2`.
3. Adopt when, on the 57 fact rows: D's unsupported rate is at most 0.10, no
   fact question is answered with nothing, and D's coverage is within 0.05 of
   A's. Otherwise record the result and take D.
4. Then the held-out answers (about USD 18) for answer support and added
   latency on v5.

Not decided here: whether analysis answers need any check (both arms 0.33
on three rows is too few to act on), and how progress answers (constraint 3)
should be evaluated at all.

## Sources

- R1: Gao et al., [RARR: Researching and Revising What Language Models Say](https://arxiv.org/abs/2210.08726), 2022.
- R2: Min et al., [FActScore: Fine-grained Atomic Evaluation of Factual Precision](https://arxiv.org/abs/2305.14251), 2023.
- R3: Gao et al., [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627), 2023.
