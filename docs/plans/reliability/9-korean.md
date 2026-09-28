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

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Fixtures | Freeze reviewed paired questions and sources | Not started |
| 2 | Comparison | Run A–D comparisons and isolate causes | Not started |
| 3 | Fix | Fix normalization only where evidence supports it | Not started |
