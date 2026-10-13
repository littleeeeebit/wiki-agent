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
Create the pair file (v1 below names it `eval/jev/korean/pairs.json` and fixes
its schema) with:

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

Arm A is that control. It runs in the same run directory as arms B–D, at the
same commit, with the same controller, policies and index settings. A and B
share the EN corpus's index, and C and D share the KO corpus's (below). The
manifest records both snapshots and which arm read which. Every claim in this
PR is a paired difference against arm A. No claim is an absolute rate held
against PR 5's gates. The report records the v4 and v8 manifests as context.
It records A's own recall beside v8's, so a drift of A from v8 is visible.
It does not interpret that drift. v8 has no answer run, so A's coverage has
nothing in v8 to compare with. Per attribution step 1, a pair whose arm A
fails is classified as a controller or baseline issue.

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
| Analysis versus facts | v8 intents with `analysis: true` (6) and fact intents with `analysis: false` phrased as advice (6) |
| Translation failure | deterministic fault fixtures, below; not a live group |

An intent belongs to one group only, and a pair reuses the intent's reviewed
English wording, evidence groups, parts and labels unchanged. A new English
intent goes through the same label review as the Korean side.

A live pair's intent has at least one evidence group and at least one part.
Recall and coverage then apply to every pair, and both gates read all 84.
That excludes v8's `direct` intents with no evidence (direct-01, 03, 05 and
43–48) and its `none` intents (none-02, 06, 07, 10–12 and 43–48), which also
have no parts. They have no file to twin either. A missing value in an
eligible pair is therefore always a failure or a gap, never a normal N/A.

Allocation is fixed, so an intent with several traits lands in one place.
Groups fill in this order: conflicting evidence, pasted documents, analysis
versus facts, names and identifiers, multi-part requests, negation and
exclusions, numbers and units. The scarce types come first. Within a group,
v8's held-out intents are taken before calibration ones. An intent goes to the
first group in that order whose trait it has and that still has room. Conflict
and names cannot fill 12 from held-out intents alone, so they take calibration
intents. The set review checks the final assignment against these rules.

Each pair adds:
- `input.ko`: a natural Korean wording of the same question, with the same
  negations, exclusions, names, numbers, units and pasted text. Pasted text
  keeps its original language where a person would paste it as is (logs,
  code, English error text).
- `meaning`, labels on the normalized English of the question, as
  `{"require": [[...]], "forbid": [...], "literals": [...]}`. `require` and
  `forbid` are `tool/eval/meaning.py`'s existing fields. `literals` is new:
  strings that must appear exactly (numbers, names, paths). `check` is
  extended to read it.
- `sources`: for every file the intent's evidence names, a Korean twin at the
  same path. The file keeps twins in one top-level `twins` map by path,
  since two intents can cite one file. Each twin carries a heading map from
  each English heading to its Korean one, so a `file#Heading` label resolves
  in both corpora. It also carries `facts`, the twin's fact units. A fact
  unit names one Korean sentence by its section and position, with `require`
  groups (a negation with what it negates, a number with its unit) and
  `literals`. A translation may merge or split sentences, so the position
  names the unit and does not align it: the unit holds when one sentence of
  its section's normalized English meets every `require` group and holds
  every literal. A negation and its object in different sentences do not
  pass. A plain `translate.kept` check does not suffice: "does not run on
  Windows" keeps "Windows" and "run" after losing "not".
- `review`: who reviewed it, which round, and when. Interpreting results
  requires this field.

Sources keep their paths, so a file-level label is the same in both corpora.
Only a section label needs the heading map. That is the blueprint's shared
`source_fact` identity, at the granularity the scorer already uses. A file
label counts any section of the file, so it is coarse. It does not stand in
for the fact units, which carry the meaning check.

### Two corpora

The EN corpus is v8's corpus at 67239d7. The KO corpus is the same corpus with
the pair files swapped for their Korean twins, about 100 files at the v8
corpus's one to two files per intent. Every other file stays English. So the
KO corpus measures Korean evidence among English neighbors, which is a mixed
repository. It does not measure an all-Korean one. That limit is stated in
the results, not hidden.

Each corpus gets one index for the whole run, built once like the
`arms` experiment's.

The evidence literal check runs on the KO corpus build, not on what an answer
happened to cite. Every twin section goes through the same evidence
normalization the index uses, and each fact unit is checked against its
sentence of the result. A lost fact therefore counts whether or not retrieval
reached it. A unit whose sentence was not normalized (failed, limited, or
missing from the output) is not a pass. It is listed, and the Literals gate is
inconclusive until it is resolved. The run reads its normalized text from the
same translation cache, so the checked English is the English the arms saw.

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
runs; each row records whether its translation was cached or fresh, and the
translator's model and prompt version. Latency and spend are read with that
in mind: a cached row pays nothing for translation.

A row records:
- the question as given, its normalized English, and the normalization status;
- the meaning-label result for the question (B, D);
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
| Literals | No required negation or literal is lost in any reviewed pair's translated question (B, D) or twin fact unit (KO corpus) |
| Integrity | No fabricated accepted citation, and no answer published as verified with a claim the grader finds unsupported, in any arm |
| Recall | For B−A, C−A and D−A, the lower end of the paired difference in evidence recall is at least −0.05 |
| Coverage | The same, for answer coverage |

A translation that failed is not a lost literal. It is its own outcome,
counted and listed. It is a defect only if it was marked translated or
continued as if normalized. But a question or fact unit that was not
normalized is not a success either. Until it is normalized and checked, the
Literals gate is inconclusive, not passed.

The cohort is frozen before the run: all 84 pairs in all four arms, at the
first run (repetition 0). The paired gates read that cohort and nothing else.
Today `report.by_intent` and `report.paired` drop rows whose value is `None`.
The implementation must not let a missing row shrink the cohort:
- a retrieval error scores recall 0 and is listed;
- an answer error (no answer published) scores coverage 0 and is listed;
- a missing grade on a published answer is unscorable. One regrade is
  allowed; if the grade is still missing, the Coverage gate is inconclusive;
- a missing row in any arm makes the Recall and Coverage gates
  inconclusive;
- an answer published as verified without a grade makes the Integrity gate
  inconclusive.

In a complete cohort of 84 pairs where no other pair differs, one pair that
loses all its evidence keeps recall above the margin (lower end −0.036), and
two do not (−0.060). That is the margin the owner chose, and the set
size follows from it rather than the other way round.

### Repetition and attribution

A pair is divergent when any arm differs from A at repetition 0 in recall,
coverage, answer status, verification status or transitions taken, or when
its question's meaning labels or one of its twins' fact units fail. A pair
is at the boundary when A itself has partial recall or partial coverage. Divergent and
boundary pairs are the candidates for two more runs.

Attribution uses steps 1–5 above, with traces and original spans kept. The
checks run in this order, and the first that applies decides:
1. A fails at repetition 0: controller or baseline issue (step 1).
2. A meaning label or fact unit fails: translation defect (step 2). This is
   decided from the labels, regardless of repetitions.
3. The pair was not repeated, or a repetition of A or of the arm is missing:
   unresolved (step 5).
4. The difference from A appears in one or two of the three runs:
   stochastic or threshold variation, not a translator defect (step 4).
5. The difference appears in all three runs and the evidence handed over
   differs in all three: evidence or index (step 3). For C and D this is the
   KO index. B shares A's index, so for B the report names it an effect of
   the normalized question's wording on retrieval.
6. Anything else, such as a difference in all three runs with the same
   evidence: unresolved (step 5).

A single differing set of evidence labels is an observation, not a cause.
Only a difference that persists over the repetitions is attributed to the
index or the wording.

A's two extra runs are shared by all arms of the pair. The shared
translation cache fixes each normalization, so repetitions measure the
controller's and host's variation, not the translator's.

Repetition is not part of the base run's approval. After the base run, the
report lists the divergent and boundary pairs with the cost of repeating them,
and repetition runs only after a second approval. A pair not repeated is
reported as unresolved (not repeated), unless check 1 or 2 already classified
it. Unresolved is not evidence of equivalence or of a sound translation.

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

84 pairs × 4 arms is 336 answer rows. v6's answer run cost USD 21.34 for 270
rows, so the base run is about USD 26.56 of host spending. The batch runner's
USD 10 ceiling splits it into about three batches. Repetition is priced
separately after the base run, from the number of divergent and boundary
pairs, and needs its own approval.

Translation covers about 84 unique Korean questions (B and D share them),
the twins' chunks, and 84 overlay answers. That is estimated at under USD 1
at `gemini-3.1-flash-lite`. It must fit the month's remaining
`TRANSLATE_MONTHLY_USD` (default 5). The balance is checked when approval is
asked and again when the run starts. Jev's tokens have no dated price; v8's
retrieval run used about 2 M for 278 rows, so roughly 3–5 M here. The base
run needs the owner's approval with this estimate.

### Order

1. This design.
2. Implementation:
   - a `pairs` experiment in `compare.py`;
   - the KO corpus build and heading map;
   - `meaning.check` extended to `literals`;
   - the fact-unit pass over every twin's normalized sections;
   - the gates file;
   - report aggregation over the frozen cohort, with the missing-value rules
     above;
   - the divergence and boundary list with its repetition cost;
   - the fault-group tests.

   It uses fake hosts only and no paid run.
3. The set:
   - `eval/jev/korean/pairs.json` and the Korean twins;
   - reviewed by the review cell in rounds until no finding remains, as the v4
     to v8 labels were (a model reviewer, gpt-6.1-sol, at the owner's
     direction, recorded as such);
   - then frozen.
4. The paid base run and its results, recorded here; then repetition, if
   approved separately.
5. A fix only where step 4 shows a translation or normalization defect.
   Every fix is checked on the failing pairs and on arm A again.

### Implementation (v1)

Order step 2, with fake hosts only:
- `tool/eval/korean.py` holds the set and every check that needs no host:
  - `load` and `invalid` validate `eval/jev/korean/pairs.json`
    (`jev-korean-pairs/1`) against its dataset by hash. The checks cover
    eligibility, `input.ko`, the meaning schema, each twin's heading map and
    fact units, and a recorded review. A twin may not have two sections of
    one name, since a chunk is named by its page and last heading.
  - `load` also refuses a set that is not seven groups of twelve, so a run
    and a report always read the whole frozen cohort. `--check` prints each
    group's calibration count. Whether the order of `GROUPS` was followed
    stays with the set review.
  - `ko_corpus` swaps the twins in; `relabel` maps a KO heading back.
  - `facts` is the fact-unit pass; `lost` is the one-sentence rule.
    - A sentence ends at its punctuation, a blank line, a heading, a table
      row or a list item; a soft line break inside a paragraph is not an end.
    - A literal counts only as a whole token, never part of a longer
      number, name, path or version. `30` is not found in `300` or
      `09:30`, `tool/x.py` not in `tool/x.py.bak`, and `v1.13.0` not in
      `v1.13.0-beta`. What joins a token is named per character: a word
      character, `/`, `\`, `-` and `@` always do; `.` does when a word
      character follows it; `,` and `:` do only between digits. A full
      stop, a clause's comma, a scheme (`mailto:`) or a label (`Port:`)
      does not. `meaning.check` uses the same rule.
    - A literal found only inside a word a hyphen joins is `uncertain`, not
      lost. `HMAC-SHA256-based` keeps the algorithm, while `v1.13.0-beta`
      is another version, and a hyphen cannot tell them apart. An uncertain
      question or fact unit leaves Literals inconclusive and settles no
      cause; only a definite loss is a translation defect.
    - A section's chunks are joined in line order. The index cuts a block
      too long for one chunk at its lines (`partial`); the next part of that
      block, on the very next line, reads on as the same paragraph. Any
      other two chunks are separate paragraphs.
  - A failed retrieval recalls 0, whatever evidence its fallback kept.
  - `Pairs` reads a run for the gates, divergence, the repetition plan and
    attribution, in the order above. The causes after step 2 assume the
    meaning was kept. An arm whose run read English other than was checked
    stays `unresolved_normalization_changed`. An arm whose meaning check is
    uncertain stays `unresolved_meaning_uncertain`. Its numbers are still
    reported.
- `tool/eval/overlay.py <run-dir>` sends arm A's answers through the display
  path, a paragraph at a time as `web/src/lib/overlay.ts` splits them, and
  writes `overlay.json`. A table must keep each row's cell count, and a list
  each item's indent and kind. A paragraph the rendering never returned
  counts as broken.
- `compare.py --experiment pairs` runs the EN corpus's units, then the KO
  corpus's, each on its own index.
  - Before the first KO unit it writes `facts.json`, the fact pass over the
    KO index's chunks. That pass uses `knowledge.english`, as `judge` does,
    with 120 seconds per call, so the arms read the cached English that was
    checked. It records the digest of each English text it checked.
  - A private source's English lives in its index's evidence store, which
    goes with each batch's scratch. The run keeps it in `private.json`
    beside its rows, never in the translator's shared cache. Every later
    batch puts it back into its new store, and so does a repetition, from
    its base run.
  - A row records its corpus and its question's translation outcome
    (status, cached, model, prompt version). The outcome is the measured
    `prepare`'s, not the warm priming call's. The row counts its evidence
    texts as cached, fresh or failed. It records every English text read,
    from priming through the answer and its grade, as text and English
    digests. A text read twice as two Englishes is listed twice.
  - A row that read other English than the fact pass checked is listed as
    drifted. Any drifted first repetition makes Literals inconclusive. A
    drifted repetition makes its arm's cause
    `unresolved_normalization_changed`.
  - A failed grade is no longer recorded as a failed answer. A pairs run
    grades once more.
  - When the two corpora share one ceiling, each batch records only its own
    spending.
  - `--plan <report.json>` runs the report's repetition plan, repetitions 1
    and 2. That run's options name it `pairs/repeat`, and the report reads
    it for attribution. The run is refused, and the report refuses it, when
    any option other than the repetitions, the plan and the base run
    differs from the base run's (the pair file, the corpora, arms, k,
    method, models, cache). The report also refuses a plan that is not the
    base run's.
- `meaning.check` reads `literals`.
- `eval/jev/korean/gates.json` holds the four gates (v1), frozen.
- `tool/test_korean.py` covers this with no network:
  - the 84-pair margin;
  - each missing-value rule;
  - the attribution order;
  - the five translator faults, through evidence normalization and through
    `prepare`'s normalize state;
  - one pairs run end to end with stand-in Jev, translator and host.

  In that run, BM25 over the English question did not reach the Korean
  twins, and paired recall reads `fail`. The stand-ins measure nothing; the
  case shows that the loss is reported, not hidden.

### The set (v1)

Order step 3. `eval/jev/korean/pairs.json` holds the set:
- 84 pairs, twelve in each group, assigned in the order of `GROUPS`.
  Calibration intents: conflict 6, names 2; every other group takes
  held-out intents only. Negation is read as the question or the answer
  turning on a negation or an exclusion ("not", "never", "only", "except",
  and "no", as in "no data engineers"). Where more held-out intents have a
  trait than a group has room for, the set takes those whose answer turns
  on it most directly.
- 88 twins with 210 fact units. Every page an intent's evidence names has
  a twin, and every other page stays English.
- Each `input.ko` keeps the question's negations, names, numbers and units.
  Pasted machine or English text (logs, code, tickets, review comments,
  dashboard lines) stays English. A quoted person (a customer, a colleague,
  an old wiki line) is in Korean, as a Korean team would have written it.
- Each pair's `meaning` labels the normalized English of the question.
  A literal is a string that must come through as written, a name, number
  or pasted span. The checks follow
  [the research for the set's labels](../../research/korean-set-labels.md)
  (`korean.missed`, `korean.check`):
  - A phrase matches inside words, as a stem, but a number in it matches
    whole: `31 days` is not in `131 days`.
  - A negation cue (`not`, `never`, `no`, `without`, `n't` and the like)
    in the same clause, up to five words before a phrase or three after it,
    governs it. A governed phrase fails a positive label, and it fails as
    the positive wording in a group of negations (`prohibited`). Limits
    such as `no more than` and `without delay` are not cues.
  - A question's `asked` groups are met on its last line. A question that
    pastes something first asks it there, so pasted words cannot stand in
    for the asked target.

  The labels follow these rules:
  - A negation is a phrase with what it negates (`never use`, `not
    shared`), never a bare `not` or `no longer`.
  - A number goes with its unit (`31 days`), and a date with its day and
    month in either order. A limit goes with its amount (`within 7`), a rate
    with its unit (`per euro`), and a change with its order (`112% to
    101%`).
  - Each independent fact of a sentence is its own unit, so a translation
    that splits the sentence passes. A negation and its object stay in one
    unit.
  - Common faithful wordings are listed beside each other: digits and
    words, `how often` and `how frequently`, `prohibited` and `may not`.
- The planted instructions in three pages (children in the office, the
  office gym, the showers) are test data. Their twins keep them,
  translated.

`korean.py --check` also runs every label on the English original it stands
for (`baseline`). Each pair's meaning is checked on its intent's English
question, and each fact unit on its section of the English page. A label
that fails there would count a faithful translation as a loss, so the check
refuses it. The set passes with none. `tool/test_korean.py` also holds
English cases against the set itself: faithful wordings that must pass and
losses that must fail. A directional sweep over every original sentence
(changed numbers, dropped and inserted negations) and its residue are on
the research page.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Fixtures | Freeze reviewed paired questions and sources | In progress — design (v1), implementation and the set above; the set's review is next |
| 2 | Comparison | Run A–D comparisons and isolate causes | Not started |
| 3 | Fix | Fix normalization only where evidence supports it | Not started |
