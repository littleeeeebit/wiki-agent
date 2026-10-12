# PR 9 — isolate Korean normalization effects

Only begin behavior changes after PR 5 establishes and freezes the English baseline.

## Experiment

| Arm | Input | Evidence | What the comparison isolates |
| --- | --- | --- | --- |
| A | Reviewed English question | English source | Frozen controller baseline |
| B | Korean equivalent through product translation | Same English source | Input normalization |
| C | Same reviewed English question | Korean source with English representation | Evidence normalization |
| D | Korean question through product translation | Korean source with English representation | Combined product path |

Use paired intents and source identities, with original citation locators. Match
negation, exclusions, names, numbers, units, pasted context and requirements.
Compare normalized meaning, chosen transition, retrieved identities, sufficiency,
answer status, citations, latency and translation cost. English explanatory prose
being fluent is not a meaning-preservation check.

Treat Korean output display as an additional presentation check rather than
confounding it with input routing. Run paired baseline repetitions to separate
model variation from translation-induced divergence. A controller defect discovered
here goes back to an English reproduction first; freeze a new baseline before
interpreting downstream translation improvements.

## Evidence and exit

Label divergences by normalization, evidence indexing, controller or stochastic
variation. Preserve failing pairs and original spans. Maintain explicit fallback
for failed translation; never mark untranslated text as successfully normalized.
The final report states which Korean scenarios are equivalent and which remain
limited rather than claiming universal parity.

## Scope and rollback

No simultaneous broad Jev tuning. Roll back translation changes independently;
retain the comparison dataset and English baseline.

## Implementation blueprint

### Fixed baseline and fixture contract

Require PR 5's baseline manifest hash and passing report; refuse execution if the
current code/policy/source versions differ without a new English control run.
Create `eval/jev/reliability/meaning-pairs.json` with:

```json
{
  "pair_id": "negation-01",
  "baseline_id": "<hash>",
  "input": {"en": "...", "ko": "..."},
  "meaning": {"required": ["..."], "excluded": ["..."], "literals": ["..."]},
  "sources": {"en_snapshot": "<hash>", "ko_snapshot": "<hash>"},
  "expected": {"allowed_transitions": ["retrieve"], "source_facts": ["f1"]},
  "review": {"kind": "human", "revision": 1}
}
```

The example review kind is not a claim that review happened. Require actual review
metadata before interpreting results. Different-language sources have different
chunk IDs; map them to shared source_fact IDs. Do not demand equal hashes for
translated text. Each citation still resolves to its own original source.

Start with paired cases from eight groups: negation/exclusions, numbers/units,
proper names/identifiers, pasted documents, multi-part requests, conflicting
evidence, analysis versus facts, and missing translation/provider failure.
Keep source revisions and graph generation fixed within each arm.

### Execution and attribution

Extend `tool/eval/meaning.py` for meaning outcomes and reuse compare/run manifests
for route/evidence outcomes; add an explicit paired experiment rather than editing
the English regression labels. Record original input, normalized English,
normalization status, route answer, evidence fact IDs, sufficiency, verification
status, citations, model calls and known/unknown cost.

Execute A/B/C/D as specified above. Compare B-A for input effect, C-A for evidence
effect, and D against both for interaction. Repeat only boundary/divergent cases
three times to estimate variation; a one-off difference is not automatically a
translator defect. Failure to normalize is its own outcome and cannot silently
enter the normal-English lane.

Attribution procedure:
1. If A itself fails, classify controller/baseline issue and return to PR 5.
2. If normalized text loses a labeled requirement/literal, classify translation.
3. If meaning survives but graph/index coverage differs, classify evidence/index.
4. If inputs and snapshots are equivalent but decisions vary on repetitions,
   report stochastic/threshold variation.
5. If insufficient evidence distinguishes causes, mark unresolved and retain traces.

### Output overlay and acceptance

Test output presentation separately after routing results are fixed. A refused
paragraph remains original with explicit status; other paragraphs may translate.
Verify tables, lists, citations, numbers and protected names survive. Do not use
back-translation alone as proof of meaning.

Owner-selected gates, frozen before the run: zero lost required negations/literals in
reviewed deterministic fixtures; zero fabricated accepted citations; no incorrect
verified labels; paired recall and answer-coverage lower confidence bounds at
least -0.05 versus the corresponding English control. This last margin is a new
translation-study criterion, not a reinstatement of the old removed Jev gate.
Any later margin change requires a new version before its measurements.

Files touched: fixture, `eval/meaning.py`, compare/report paired aggregation,
and only the demonstrated `translate`/normalization defect. Run the relevant
translation fixture plus the frozen English regression subset after a fix.
If controller code changes, rerun/version PR 5 first. Rollback the translator
change independently of retained experiment records.

## v1 — the paired experiment (design)

This section settles what the blueprint above leaves open, so the
implementation, the set and the run can follow it without new choices.

### The English control

The blueprint requires PR 5's baseline. PR 5 closed with the routing and
answer gates passed on some sets and work start inconclusive at 52 of 54
(v8). Its recorded baseline for this PR, `reliability-v4` at a0e88d3, ran on
code that has since changed: the answer path (#114 onward), PR 6's review
profiles, PR 7's planner and PR 8's recovery. A run on today's code is
therefore not the measurement the v4 report describes.

Arm A is that control. It runs in the same run directory, at the same commit,
with the same policies and the same index as arms B–D. Every claim in this
PR is a paired difference against arm A. No claim is an absolute rate held
against PR 5's gates. The report records the v4 and v8 manifests as context.
It records A's own recall and coverage beside them, so a drift of A from v8
is visible. It does not interpret that drift. Per attribution step 1, a pair
whose arm A fails is classified as a controller or baseline issue.

### Pairs

There are seven live groups of 12 pairs each (84 pairs) and one fault group:

| Group | Source of the English intent |
| --- | --- |
| Negation and exclusions | v8 intents whose question or answer turns on "not", "except", "only", "never"; new ones if fewer than 12 |
| Numbers and units | v8 intents with a number, unit, size or duration in the reference; new ones to reach 12 |
| Names and identifiers | v8 intents whose reference names a path, flag, port, service or version |
| Pasted documents | v8 `pasted` intents |
| Multi-part requests | v8 intents with two or more `parts` |
| Conflicting evidence | v8 `conflict` intents |
| Analysis versus facts | v8 `analysis` intents (6) and fact intents phrased as advice (6) |
| Translation failure | deterministic fault fixtures, below; not a live group |

An intent belongs to one group only, and a pair reuses the intent's reviewed
English wording, evidence groups, parts and labels unchanged. A new English
intent goes through the same label review as the Korean side. The set takes
the English intents from v8's held-out split first. A calibration intent is
used only where the held-out split has too few.

Each pair adds:
- `input.ko`: a natural Korean wording of the same question, with the same
  negations, exclusions, names, numbers, units and pasted text. Pasted text
  keeps its original language where a person would paste it as is (logs,
  code, English error text).
- `meaning`, labels on the normalized English of the question: `required`
  groups, `excluded` phrases and `literals`. `tool/eval/meaning.py`'s
  `check` reads them unchanged.
- `sources`: for every file the intent's evidence names, a Korean twin at the
  same path. Each twin carries a heading map from each English heading to its
  Korean one, so a `file#Heading` label resolves in both corpora. It also
  carries `literals`, the strings that must survive in the twin's `text_en`
  (numbers, names, negations).
- `review`: who reviewed it, which round, and when. Interpreting results
  requires this field.

Sources keep their paths, so a file-level label is the same in both corpora.
Only a section label needs the heading map. That is the blueprint's shared
`source_fact` identity, at the granularity the scorer already uses.

### Two corpora

The EN corpus is v8's corpus at 67239d7. The KO corpus is the same corpus with
the pair files swapped for their Korean twins, about 100 files at the v8
corpus's one to two files per intent. Every other file stays English. So the
KO corpus measures Korean evidence among English neighbors, which is a mixed
repository. It does not measure an all-Korean one. That limit is stated in
the results, not hidden.

Each corpus gets one index for the whole run, built once like the
`arms` experiment's.

### Arms

All four arms run the product path that `compare.py`'s arm D runs: the Jev
controller, the graph lane and claim verification, at `--level answer`.

| Arm | Question | Corpus |
| --- | --- | --- |
| A | `input.en` | EN |
| B | `input.ko` | EN |
| C | `input.en` | KO |
| D | `input.ko` | KO |

The same independent grading session grades every arm against the pair's
English parts. The translator's cache is shared on disk, as in the existing
runs; cached and fresh translations are recorded per row.

A row records:
- the question as given, its normalized English, and the normalization status;
- the meaning-label result for the question (B, D);
- the literal result for each cited chunk's `text_en` (C, D);
- the transitions taken;
- the evidence labels handed over (mapped through the heading map);
- sufficiency, answer status, citations and grading;
- latency, and host, translator and Jev spend.

`report.py`'s per-intent paired bootstrap (`paired`, 2000 resamples, 95%,
fixed seed) reads the arm letters unchanged. It gives the B−A, C−A and D−A
differences, and D against B and C for interaction.

### Gates

The owner selected these gates in the blueprint. They are frozen in
`eval/jev/korean/gates.json` before the run. Any later change is a new
version before its measurement.

| Gate | Pass when |
| --- | --- |
| Literals | No required negation or literal is lost in any translated question (B, D) or cited chunk (C, D) of a reviewed pair |
| Integrity | No fabricated accepted citation, and no answer published as verified with a claim the grader finds unsupported, in any arm |
| Recall | For B−A, C−A and D−A, the lower end of the paired difference in evidence recall is at least −0.05 |
| Coverage | The same, for answer coverage |

A translation that failed is not a lost literal. It is its own outcome,
counted and listed. It is a defect only if it was marked translated or
continued as if normalized. With 84 pairs, about one pair that loses all its
evidence keeps recall above the margin, and two do not. That is the margin
the owner chose, and the set size follows from it rather than the other way
round.

### Repetition and attribution

A pair where any arm's recall or answer status differs from A gets two more
runs of A and of that arm. A difference in one run out of three is reported
as stochastic or threshold variation, not as a translator defect. A
difference in three out of three goes through attribution steps 1–5 above,
with its traces and original spans kept.

### Translation failure

The fault group needs no network. It covers:
- the translator raising;
- the monthly limit at zero;
- a deadline shorter than the call;
- a batch returned with the wrong count;
- protected spans not restored.

Each fixture runs through `knowledge.prepare`'s `normalize` state and through
evidence normalization. Each must end with an explicit `failed` or `limited`
status, with English text absent where the status says so (the
`search.evidence.problems` contract) and a visible fallback. A fixture fails
if the untranslated text is marked as normalized. These fixtures are tests in
the implementation PR, not a paid run.

### Output overlay

This is a separate check after the routing results. It runs arm A's answers
through the Korean display path. Tables, lists, citations, numbers and
protected names must survive, checked with `translate`'s own `protect`,
`intact` and `facts`. A refused paragraph stays original with its status. No
back-translation is used as proof. It sends only the answers, to the
translator.

### Cost and limits

84 pairs × 4 arms is 336 answer rows. At v6's held-out rate (USD 21.34 for
its answer rows), that is about USD 25–30 of host spending. Repetition adds
about USD 2. The batch runner's USD 10 ceiling splits the answer run into
about three batches. Translation of about 170 questions and 100 files plus
the overlay check costs under USD 1 at `gemini-3.1-flash-lite`. That must fit
the month's remaining `TRANSLATE_MONTHLY_USD` (default 5), which the run
checks first. Jev's tokens have no dated price; v8's retrieval run used about
2 M for 278 rows, so roughly 3–5 M here. The paid run needs the owner's
approval with this estimate.

### Order

1. This design.
2. Implementation:
   - a `pairs` experiment in `compare.py`;
   - the KO corpus build and heading map;
   - question and evidence literal checks;
   - the gates file;
   - report aggregation;
   - the fault-group tests.

   It uses fake hosts only and no paid run.
3. The set:
   - `eval/jev/korean/pairs.json` and the Korean twins;
   - reviewed by the review cell in rounds until no finding remains, as the v4
     to v8 labels were (a model reviewer, gpt-6.1-sol, at the owner's
     direction, recorded as such);
   - then frozen.
4. The paid run and its results, recorded here.
5. A fix only where step 4 shows a translation or normalization defect.
   Every fix is checked on the failing pairs and on arm A again.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Fixtures | Freeze reviewed paired questions and sources | In progress — design (v1) above |
| 2 | Comparison | Run A–D comparisons and isolate causes | Not started |
| 3 | Fix | Fix normalization only where evidence supports it | Not started |
