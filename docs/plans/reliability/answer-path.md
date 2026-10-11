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

## Analysis path

Analysis questions, a class calibration barely held, are the largest part
of both failures but not all of answer support's: without them, the fact
and abstain rows still give D 35/241 against A's 84/447, 22.7% below, short
of 25%. That is a diagnostic split, not a proposal to change the v5 gate.
This section weighs what to change; nothing here is decided.

### Latency

`Flow.ask` hands the host to `decision.decide` for every request. Of a
judge request's kinds only coverage is the host's to settle; useful,
conflict, redirect and repair are code's (`CODE_SETTLES`). The route sets
`dossier["analysis"]` before any judge request.

| Option | Change | Effect | Cost |
| --- | --- | --- | --- |
| L1 | No host fallback in a judge request when the route marked the question analysis | Removes the coverage turns, 187 of the 305 analysis coverage questions | An uncertain coverage stays uncertain, which changes the evidence the synthesis reads (below), and reaches the fact questions the route marks analysis |
| L2 | L1, and drop progress from the route's host turn once Jev says analysis yes | Also removes most of the 27 progress turns on analysis rows | `decision.decide` asks every uncertain question in one host turn; this needs the route to withhold progress, a change to a shared contract |
| L3 | One retrieval round for analysis questions | Fewer rounds, but retrieval outside the host's turns takes about 1 s per question across all its rounds; what removing rounds saves is left to measurement | Less evidence: the 40 rows at the round limit had recall 0.85 after three rounds |

L1 is the smallest change that reaches the cause. The bound computed above,
−3.23 s with all set-aside time removed from analysis rows, includes the
route's turn, so L1 alone saves less; how much is measured, not assumed.

An analysis answer is published unverified either way, but L1 changes what
it is written from. A coverage yes keeps the passages it judged from being
dropped and holds them past k (`Flow.judge`, `rested`); left uncertain, they
can be dropped, the requirement counts as missing, and repair runs on,
retrieving other passages and sources. The synthesis brief carries the
evidence, the retrieval status and the missing requirements
(`Grounding.view`), so all three can differ, and answer support and
coverage must be measured, not only latency.

The route's flag, not the intent's label, decides L1. In the held-out run
it marked eight fact intents analysis: `analysis-85`, `-86`, `-89`, `-90`,
`pasted-40`, `start-33`, `start-35` and `fix-31`. `start-35` ends `ready`
today with one of its three coverage questions settled by the host; under
L1 that one stays uncertain. These rows lose the host's coverage too, so a
measurement of analysis intents alone cannot show L1 is safe.

### Answer support

Pooled, D needs at most 0.131 unsupported (A's 0.174 less 25%) and has
0.158, 117 of 741 claims. Analysis rows hold 82 of the 117, and A's
analysis answers are unsupported at nearly the same rate, 0.161 against
0.164.

Fourteen of the 82, drawn with `random.seed(1)` and `random.sample` over
the claims in record order, read as follows (intent and claim index):

| Reading | Claims |
| --- | --- |
| A repository fact | `analysis-79` 3, `analysis-65` 4, `analysis-47` 3 ("No breakdown of the misses was recorded.") |
| Inference | `analysis-42` 9, `analysis-37` 5, `analysis-54` 12 ("A freeze makes each later release bigger, which can add risk."), `analysis-41` 10, `analysis-74` 8, `analysis-65` 8 |
| Outside knowledge | `analysis-74` 11, `analysis-71` 1 ("A /24 is a block of 256 adjacent IP addresses.") |
| Arithmetic over given figures | `analysis-68` 5, `analysis-40` 4, `analysis-34` 4 |

The reading is one reviewer's, of one small sample; it describes these 14,
not the 82.

| Option | Change | Effect | Cost |
| --- | --- | --- | --- |
| S1 | Check an analysis answer's repository-fact premises as fact claims are checked, and drop or mark the unsupported | Reaches premises that state repository facts | Telling a premise from inference is a new classification; inference it leaves alone |
| S2 | A stricter synthesis brief: separate stated facts, each from a passage, from reasoning, and keep outside knowledge out unless asked | Fewer outside-knowledge claims | A prompt change, measured only by another run; the grader may still count inference as unsupported |
| S3 | Leave analysis answers as they are; record the pooled gate as failed in v5 | No change | v6 must decide whether a pooled unsupported rate is the right gate for a cohort that is 43% analysis; a gate is not changed after its held-out run |

Bringing analysis to 0.12 (about 22 fewer unsupported claims) would pass
the pooled target with the other rows unchanged. Neither S1 nor S2 has been
measured.

### Recommendation

L1, measured on the full calibration answers and then the full held-out
answer cohort, so that the fact intents the route marks analysis are in
both, and judged on latency, answer support and coverage. For answer
support in v5, S3: the failed result stands. The gate's cohort and the
treatment of analysis inference are set for v6 before its held-out run.

## L1 answers at 01702da

L1 (#118) was measured on the full calibration answers
(`raw/eval/jev/reliability-v5-calibration-answers-l1`, USD 9.50, 38.7
minutes) and then the full held-out answers
(`raw/eval/jev/reliability-v5-heldout-answers-l1`, three batches, USD 21.19,
89.2 minutes). Each is compared with the last run of the same split: b3
(ebbe420) for calibration and ac90615 for held-out. In `tool/` and `eval/`,
both differ from 01702da in L1 alone, so a difference is L1's or the runs'
own variation.

L1 was designed from the held-out run's diagnosis, so this second held-out
run is no longer an unseen test of it. It shows what L1 changes on the same
rows; it does not replace v5's recorded held-out result, which stays failed.
A clean test of the answer path is v6's held-out run.

### Calibration

| Run | Added latency (s) | D p95 | A p95 | Answer support | D unsupported | A unsupported |
| --- | --- | --- | --- | --- | --- | --- |
| b3 | 11.073 | 30.798 | 19.725 | 0.319 | 0.0963 (n=218) | 0.1414 (n=474) |
| L1 | 4.398 | 20.612 | 16.214 | 0.5795 | 0.0756 (n=238) | 0.1798 (n=484) |

Rows on both sides of L1's path became faster, and these runs do not
separate their shares. Among b3's slowest D rows were fact rows whose answer
step ran long (`fix-03` 36.2 s of 40.9 s total, `fix-12` 26.5 of 30.8,
`adv-09` 21.2 of 32.1); L1 does not touch them, and in the L1 run each took
22 s or less in total. L1 changes only rows the route marks analysis, and
calibration has three; one of them, `analysis-03`, was b3's second-slowest D
row at 34.0 s and took 18.3 s under L1. Putting only these three L1 rows in
place of b3's lowers b3's D p95 from 30.798 to 25.472 s, 5.3 s of the
observed 6.7 s fall; that mix carries those rows' own run variation, so it
bounds nothing either way.

| Row | Retrieval / set aside, b3 (s) | Retrieval / set aside, L1 (s) | Status b3 → L1 | Evidence b3 → L1 | Unsupported b3 → L1 |
| --- | --- | --- | --- | --- | --- |
| `analysis-02` | 6.4 / 5.1 | 3.0 / 0.0 | ready → partial | 10 → 4 | 0/11 → 1/7 |
| `analysis-03` | 15.3 / 14.5 | 1.0 / 0.0 | ready → partial | 16 → 17 | 7/16 → 2/11 |
| `analysis-06` | 4.8 / 3.8 | 5.6 / 4.6 | partial → partial | 16 → 16 | 4/12 → 2/15 |

Coverage went to the host 4 times in 11 on these rows in b3 and 0 in 13
under L1; `analysis-06` still sets 4.6 s aside, for its route's progress
question. `analysis-02` kept 4 passages where b3 kept 10, the loss
of evidence protection the design expected. The grader marked every part
answered in both runs. The 57 fact rows are unchanged: unsupported 8/147
(0.054) in b3 and 9/165 (0.055) under L1. The route marked no calibration
fact row analysis in either run.

### Held-out

| Run | Added latency (s) | D p95 | A p95 | D p50 | A p50 | Answer support | D unsupported | A unsupported |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ac90615 | 17.49 (fail) | 42.592 | 25.102 | 17.334 | 11.932 | 0.093 (fail) | 0.1579 (n=741) | 0.1741 (n=919) |
| L1 | 0.283 (pass) | 22.320 | 22.037 | 14.550 | 12.931 | 0.2301 (fail) | 0.1412 (n=765) | 0.1834 (n=954) |

The route marked the same 62 rows analysis in both runs (54 analysis
intents and 8 fact intents). Paired by intent, their medians:

| Per row (s) | ac90615 | L1 | Paired difference |
| --- | --- | --- | --- |
| Retrieval | 15.17 | 3.26 | −12.44 |
| Set aside for host turns | 13.89 | 1.51 | −12.48 |
| Answer step | 13.87 | 14.16 | −0.26 |
| Total | 28.68 | 17.04 | −11.46 |

Coverage on these rows went to the host 188 times in 319 at ac90615 and 0
in 362 under L1. What is still set aside is the route's progress (27 turns),
route (3) and ask (2) questions. The fall in retrieval follows the fall in
set-aside time, which is L1's mechanism; the gate value is one rerun's
outcome, not L1's exact effect, since A's p95 also moved, from 25.102 to
22.037 s. The cost is the one the design named: of the 54 analysis rows, 13
ended `ready` at ac90615 and 2 under L1, and 51 ran three rounds where 43
had. The evidence kept by the 54 had a median of 21 passages at ac90615 and
20 under L1 (19 and 18 over all 62), and the median paired difference is 0
in both cohorts. The grader's coverage of the 54 is 0.972 in both runs.

By intent class, answer support:

| Rows | D, ac90615 | D, L1 | A, ac90615 | A, L1 |
| --- | --- | --- | --- | --- |
| Analysis | 82/500 (0.164) | 86/513 (0.168) | 76/472 (0.161) | 88/490 (0.180) |
| Other | 35/241 (0.145) | 22/252 (0.087) | 84/447 (0.188) | 87/464 (0.188) |

D's analysis intents have a similar rate in both runs. The pooled value,
0.093 then 0.230, moved with all four cells, most with A's analysis rows (12
more unsupported claims) and D's other rows (13 fewer). Of D's other rows,
the 8 fact intents the route marks analysis take L1's path: their
unsupported claims fell from 13/55 to 7/53, and their coverage, which went
to the host once in 14 at ac90615, was asked differently under L1
(`start-35` asked three times, then six). The 58 fact rows L1 does not touch
went from 18/158 to 15/173. One rerun neither attributes the pooled
improvement to L1 nor rules L1 out of it. Neither run passes the 0.25
target. S3 stands: v5's answer-support result is failed, and v6
sets the gate's cohort and the treatment of analysis inference before its
held-out run.

### Reading

L1 removes the host coverage turns from analysis rows, and in this rerun
the held-out latency gap closed with them. The analysis intents' unsupported
rate (0.164, then 0.168) and the grader's coverage (0.972 both) were
similar; one rerun does not establish that they are equal. What L1 gives up
is the `ready` status: an analysis now ends `partial` by the round limit
where a host turn used to accept its coverage. These runs do not separate
what that status changes in synthesis.

## Sources

- R1: Gao et al., [RARR: Researching and Revising What Language Models Say, Using Language Models](https://arxiv.org/abs/2210.08726), 2022.
- R2: Min et al., [FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://arxiv.org/abs/2305.14251), 2023.
- R3: Gao et al., [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627), 2023.
