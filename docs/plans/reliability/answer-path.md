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
is verified, Jev's own or the host's where the route's host fallback settles
what Jev left uncertain; yes, uncertain or an unavailable decision
synthesizes as today. A wrong no on a progress question is the risk: the
check then leaves the question unanswered and falls back to synthesis, or
publishes the parts it verified without the repository observations. v5 has
no progress intents to measure either.
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
`reliability-v5-calibration-answers-b` (A and D, English) in two batches at
the same commit: the first stopped at its USD 10 ceiling after 128 of 138
rows, the second ran the last 10 (46 minutes and USD 10.68 in all).

| Fact rows | A | D under B |
| --- | --- | --- |
| Rows | 57 | 57 |
| Claims | 361 | 182 |
| Unsupported | 59 (0.163) | 6 (0.033) |
| `answer.status` abstained | 0 | 0 |
| Grader abstained | 0 | 1 |
| Coverage | 0.991 | 0.974 |

Three of the four adoption checks hold: the unsupported rate is under 0.10,
nothing is recorded `abstained`, and coverage is 0.017 under A's. The
fourth does not: the grader found D's `fix-12` answer unanswered and none of
A's. That row fell back to synthesis, and the synthesis said no project rule
on byte-order marks was found: D's retrieval did not reach
`hub/craft/utf8-files.md`, which A found with its own search. The same row
was grader-abstained under synthesis in `reliability-v5-calibration-answers-2`,
so the miss is retrieval's, not B's, but the rule as written is not met.

The purpose decision said no on 56 of the 57 fact rows and uncertain on
`memory-08`, which was synthesized. Ten of those 56 were Jev's uncertain
verdict settled no by the host through the route's fallback (`route-05`,
`memory-02`, `memory-03`, `memory-09`, `memory-11`, `adv-05`, `adv-08`,
`analysis-10`, `pasted-04`, `start-11`; 25 claims, none unsupported).
Review round 1 of #112 kept `progress` from the host as a departure from
"only a sure no"; the owner chose to keep the host's settlement instead, so
the measured path is the current one. Had those ten been synthesized, their
synthesis in `reliability-v5-calibration-answers-2` (15 of 76 unsupported)
would put the fact rate near 0.09; that is an estimate across two runs, not
a measurement. The other round 1 repairs (the brief after a rebase, the
no-usable-answer guard, marks in checked prose) change no recorded row: no D
answer carried an evidence mark or was published empty. Three fact rows fell back (`bridge-10`,
`bridge-11`, `fix-12`, all v4 abstentions), as did five of the six abstain
intents; those five published no unsupported claim. Analysis rows were
synthesized as before (D 15 of 44 unsupported, A 7 of 30).

Latency is the cost. Added latency, D's answer p95 over A's, is 16.73 s
(D 33.91, A 17.18) against the gate's 10 s; it was 6.25 s for v4's
calibration and −3.37 s for `reliability-v5-calibration-answers-2`. In the
first batch, verified single-turn rows had a median of 14.7 s against A's
8.8 s, and 13 of 64 D rows took a second host turn (6 repairs, median
31.0 s; 7 fallbacks, median 17.9 s). A's own p95 moved from 24.09 s to
17.18 s between the two v5 runs, so the gap is partly the host's variance,
but the held-out added-latency gate is at risk.

The owner adopted B on 2026-10-11 with the fourth check unmet, because the
row fails the same way without B. The held-out answers decide answer support
and added latency.

## Latency

Where D's time went in `reliability-v5-calibration-answers-b`. No row
records per-step time, so this is observational. Of D's 225 host turns, 81
drafted answers, 66 graded them after the answer (outside the measured
time), 25 settled retrieval decisions and 53 settled claim decisions. 46 of
66 D rows sent a claim decision Jev left uncertain to the host (111
questions, 98 settled; questions in one request share a turn). A
least-squares fit of the answer step's own time (retrieval excluded) on
drafting turns and claim-stage host turns gives about 7.3 s for each
(R² 0.65). Subtracting the claim-stage turns at that rate would put added
latency near 8.1 s; also removing the repair turn, near 4.2 s; removing only
the repair turn, near 14.8 s. These are estimates from the fit, not
measurements, and they hold the rest of each answer fixed.

The owner chose to drop the host from an ordinary answer's claim checks: a
claim Jev leaves uncertain is withheld, and a question left unanswered falls
back to synthesis. 26 D rows published host-settled claims, so coverage may
fall and the fallback rate rise; the calibration answers are measured again
on that change before the held-out answers.

Measured at d83b98c as `reliability-v5-calibration-answers-b2`, all 138
rows in one batch (39 minutes, USD 9.48):

| Fact rows | A | D, no host in claim checks |
| --- | --- | --- |
| Rows | 57 | 57 |
| Claims | 387 | 168 |
| Unsupported | 68 (0.176) | 10 (0.060) |
| `answer.status` abstained | 0 | 0 |
| Grader abstained | 0 | 1 |
| Coverage | 0.991 | 0.982 |

Added latency fell from 16.73 s to 3.48 s (D p95 20.42, A 16.93); D's
median answer time is 10.8 s against A's 8.7 s. No D row was host-checked.
Five fact rows fell back to synthesis (`direct-01`, `bridge-10`,
`bridge-11`, `adv-05`, `fix-12`), as did five of the six abstain intents.
The unsupported rate rose from 0.033 to 0.060; 7 of the 10 unsupported
claims are in `route-05` and `pasted-04`, whose progress verdict stayed
uncertain this run and which were synthesized. The grader-abstained row is
`fix-12` again, the same retrieval miss. Three of the four adoption checks
hold, as before.

## Retrieval misses

In `-b2`, 6 of D's 63 rows with expected evidence lacked some of it. Four
are fault rows, by design. The other two were reproduced on a local index
of the same corpus, with no request sent:

| Intent | Cause | Ranks in round 1 |
| --- | --- | --- |
| `fix-12` | The question says "BOM"; `hub/craft/utf8-files.md` says only "UTF-8 without a byte-order mark". A subquery round searches the requirement, here the question itself, so it misses the same way. | BM25 82, e5 45, RRF 48; "byte-order mark rule for text files" ranks it 1 in both |
| `analysis-03` | `repo/docs/traffic.md` ranks 7 at k = 8, but each of four sources' floor took a slot. Round 2's two subqueries got four slots each, and the floors took all four, leaving out `traffic.md`, ranked 2 in the documents. Both subqueries returned `papers/splade.md`; `merged` counted it twice, so round 3's `spent` passed its seen ids. Every round-3 request was refused as invalid, and `bounded` reported the error as `retrieval_unavailable`. | RRF 7 |

The second cause ended every v5 run's `analysis-03` and `analysis-06`, and
the held-out `analysis-56`, `-69` and `-78`, in `retrieval_unavailable`;
the graded answers were still `answered`. `merged` now counts a chunk two
siblings returned once. A repair round's seed slots fewer than twice the
sources now have no floor, so a subquery's four slots go by rank, and its
round 2 returns `traffic.md`. Round 1 keeps its floor unchanged, at least
one per source while there is a slot for each, since dropping it would
leave a low-ranked source out of grading once all five families are
allowed at k = 8. `fix-12` needs a different query wording, which no repair now
proposes; it is left open.

Measured at ebbe420 as `reliability-v5-calibration-answers-b3`, the same
138 rows (41 minutes, USD 9.65):

| | b2 (d83b98c) | b3 (ebbe420) |
| --- | --- | --- |
| D fact unsupported | 10/168 (0.060) | 8/147 (0.054) |
| A fact unsupported | 68/387 (0.176) | 60/385 (0.156) |
| D fact coverage | 0.982 | 0.974 |
| D mean recall, 57 rows with expected evidence | 0.974 | 0.982 |
| D retrieval seconds p50 / p95 | 0.57 / 6.94 | 0.56 / 6.94 |
| Answer-step seconds p95, D / A | 16.5 / 16.9 | 20.4 / 19.7 |
| Added latency | 3.48 s | 11.07 s |

Only `analysis-03` and `analysis-06` took another path. `analysis-03` now
retrieves `traffic.md`, reaches recall 1.0 and ends `ready`. `analysis-06`
ends at the round limit (`rounds`) instead of `retrieval_unavailable`.
Both are still graded answered. The added latency rose above the 10 s gate.
The answer step is the whole `grounded` flow, drafting and Jev's claim
checks together; the records hold no host time apart from it. Its p95 rose
in both arms. `fix-03`'s answer step took 36.2 s against 9.7 s, with one
draft turn and two verification batches in each run, so the host is a
likely but unmeasured cause. Retrieval's p95 is unchanged, but rows moved:
`adv-09` from 6.9 to 10.9 s, `analysis-03` from 11.6 to 15.3 s, and
`fix-12`, D's p95 row in both runs, from 3.9 to 4.3 s with other evidence.
Pairing each row's b3 retrieval with its b2 answer step gives 6.18 s of
added latency, and the reverse pairing 8.42 s, so the rows' pairing matters
and neither stage is ruled out. One batch per revision cannot separate
them. The gate is judged on the held-out cohort.

## Held-out answers at ac90615

`reliability-v5-heldout-answers`, 258 rows in three batches (USD 24.83;
the estimate of USD 18 assumed calibration's cost per row, and D's analysis
rows cost USD 0.174 on average). `tool/eval/report.py` judges both answer
gates failed:

| Gate | Target | Value |
| --- | --- | --- |
| Answer support, D below A | 0.25 | 0.093 |
| Added latency | 10.0 s | 17.49 s |

| Class | Rows | A unsupported | D unsupported | D answer seconds p50 / p95 | A answer seconds p50 / p95 |
| --- | --- | --- | --- | --- | --- |
| Fact | 66 | 79/404 (0.196) | 31/213 (0.146) | 10.7 / 20.0 | 8.1 / 19.2 |
| Analysis | 54 | 76/472 (0.161) | 82/500 (0.164) | 31.4 / 43.5 | 15.8 / 26.6 |
| Abstain | 6 | 5/43 | 4/28 | 15.4 / 22.6 | 10.2 / 12.8 |

Answer seconds are retrieval and the answer step together, at the report's
percentile. Of the 126 graded intents, 54 (43%) are analysis, against 3 of
66 (5%) in calibration. Analysis answers are synthesized and never checked,
so they carry D's unsupported rate to A's. On fact rows alone D is 25.6%
below A, at the target, though its 0.146 is far above calibration's 0.054.
Its 31 unsupported claims split by path: 13 in 47 verified answers (44
complete, 3 partial), 5 in 11 that fell back to synthesis, and 13 in 8
published unverified with no fallback.

D's analysis rows spend a median 17.6 s retrieving (32.4 s at most); 43 of
54 run three rounds and 40 end at the round limit. The host fallback turns
the budget sets aside take a median 16.5 s of it, and the rest, local
retrieval and Jev's requests, a median 1.1 s: the judge's coverage questions fell to the host 187 of 305 times on
analysis rows, against 4 of 99 on fact rows, and the route's progress 27 of
54. Subtracting the set-aside time from the analysis rows alone gives
−3.23 s of added latency; that is arithmetic on recorded times, not a
measurement of a run without those turns.

The retrieval-only held-out run at 21dff77 took a median 13.1 s on the same
rows, with 37 of 54 at the round limit and 11.9 s set aside. Between the
two runs, routing gained the progress question and its host fallback as
well as #114's retrieval changes, so the rise to 17.6 s is observed, not
attributed. The three rows that ended `retrieval_unavailable` there end at
the round limit here.

## Sources

- R1: Gao et al., [RARR: Researching and Revising What Language Models Say, Using Language Models](https://arxiv.org/abs/2210.08726), 2022.
- R2: Min et al., [FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://arxiv.org/abs/2305.14251), 2023.
- R3: Gao et al., [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627), 2023.
