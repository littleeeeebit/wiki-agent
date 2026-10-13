# Meaning labels for the Korean set

Reliability PR 9 counts a Korean translation as a loss when its normalized
English fails the set's labels. The labels must reject a translation that
changed a number, flipped a negation or lost a condition. They must accept the
common faithful wordings. This page is PR 8's research step for the set review
(#135). The same P1 class came back in rounds 1 and 2: research and fix cycle
1 of at most 2. It came back again in round 3: cycle 2, the last (see
"Cycle 2").

## Problem and invariant

The invariant: a label holds on the English original and on faithful wordings,
and fails on a change of number, unit, date, polarity, bound or asked target.

Round 1 found losses that passed and faithful wordings that failed. The fix
rewrote the labels set-wide: negation phrases bound to their object, numbers
bound to their units, independent facts split, faithful alternatives listed.
Round 2 found the same class again in six forms:

1. A number phrase matched inside a longer number. `31 days` was in
   `131 days`, `1 March` in `11 March`, `2` in `12`.
2. The opposite fact passed. `have alt text` was in `does not have alt text`.
   A bare `no longer` passed `no longer disabled`. A positive alternative inside
   a negation group passed the positive fact.
3. A condition was checked as separate words. `7 working days` passed
   `after 7 working days`. `one point`, `euro` and `per` passed
   `one point per two euros`. `went from` with two literals passed a rise.
4. Faithful wordings failed: `How do I handle`, `causing`, `prohibited`,
   `Keep ... out of`, `worksheet`, `upcoming`.
5. Bundled units failed a faithful split.
6. Pasted context satisfied the asked target. The quoted `Shared carts`
   satisfied a question about a `private cart`.

The round 1 fix was label-only, and it failed for one reason. Labels are
substring checks. No list of alternatives can encode a number's boundary or
a cue's scope, so each new alternative opened another hole.

## Sources

Searched on 2026-10-13. The queries were machine translation critical errors,
number and negation errors in translation, negation scope detection, and
behavioural testing of NLP models.

| ID | Source | What it establishes, with its conditions |
| --- | --- | --- |
| S1 | [WMT21 critical error detection: categories](https://statmt.org/wmt21/quality-estimation-task_critical-error-examples.html) | Two critical error types. NUM: a number, date, time or unit translated incorrectly or removed. SEN: a negation introduced or removed, with or without an explicit negation word, or the sentiment reversed. Partial or minor deviations that keep the meaning are not errors. |
| S2 | [Wang et al. 2021, "As Easy as 1, 2, 3"](https://arxiv.org/abs/2107.08357) | Numerical errors are widespread across commercial and research translation systems. The mitigations treat numbers separately and add sanity checks. These are suite-level findings, not a checker design. |
| S3 | [Hossain et al. 2020, "It's not a Non-Issue"](https://arxiv.org/abs/2010.05432) | Translation of negation remains an error source. Negation is detected through cues and their scope. |
| S4 | [Tang et al. 2021, "Revisiting Negation in NMT"](https://arxiv.org/abs/2107.12203) | Negation errors are insertions or deletions of cues. Deletion is the harder case for systems and evaluation. Its contrastive evaluation follows LingEval97's polarity category. |
| S5 | Chapman et al. 2001, NegEx ([J. Biomed. Inform. 34(5)](https://doi.org/10.1006/jbin.2001.1029), §2.1, §4, Appendix) | Rule-based negation of findings already indexed as clinical terms (UMLS). Pre-triggers and post-triggers each negate a term at most five intervening tokens away, a token being a word or a UMLS term. Pseudo-negation phrases negate nothing; the 2001 list holds its own phrases, not the ones this page uses. §4 reports false positives where a negation reaches an adjacent, separate proposition. |
| S8 | Harkema et al. 2009, ConText ([J. Biomed. Inform. 42(5)](https://doi.org/10.1016/j.jbi.2009.05.002)) | NegEx's successor. A trigger's scope runs to the sentence's end or to a termination term (such as `but`), not over a fixed window. |
| S6 | [Ribeiro et al. 2020, CheckList](https://arxiv.org/abs/2005.04118) | Behavioural testing with three test types. Minimum functionality tests check targeted cases. Invariance tests apply perturbations that must not change the result. Directional tests apply perturbations that must change it. Templates and perturbations find failures that held-out accuracy hides. |
| S7 | [Xbench QA features](https://docs.xbench.net/user-guide/work-qa-features/) | Translation QA ships a numeric mismatch check between source and target. The page establishes that the check exists, not how it draws token boundaries. |

## Core theory

S1 and S4 define the losses that matter as a number change or a polarity change
(interpretation: the set's losses match the WMT critical categories). S2 shows
that numbers need their own check, and S7 that translation QA has one
(interpretation: a substring check is not one). S3, S5 and S8 show that
polarity is a property of a cue and its scope, not of the words present. A check that only finds a phrase cannot tell `has alt text` from
`does not have alt text`. S6 supplies the test method: directional
perturbations that must flip the outcome, and faithful rewordings that must not.

## Applicability

The checker runs on short English rule and fact sentences, written by the
translator from a known Korean source. S5 and S8 were built and measured on
clinical findings, not on policy sentences matched as substrings. Carrying
their window and terminators here is this page's own inference, and the
sweeps below are its only test (interpretation). Numbers in the set are integers, decimals, comma thousands,
times and percentages, the formats `meaning.joins` already treats as one
token for literals.

## Counterevidence and limits of the sources

- S5's window misses scope across coordination (`has not a plain-text version
  and an unsubscribe link`) and across long clauses. The same misses appear
  here (see Result).
- S3 and S4 measure systems, not checkers. They say what errors occur, not how
  well a cue rule finds them.
- A cue rule can count a faithful sentence as a loss. A legitimate negation of
  another fact can sit in the same clause (`reimbursed with the payroll and not
  separately`). The window and the terminators limit this, and they do not
  remove it.

## Alternatives considered

- More alternatives per label: the round 1 approach. Round 2 showed it does not
  converge (Problem).
- A model judge for meaning: rejected. PR 9's design keeps the translating
  model from grading its own output, and a second model would add cost and its
  own variance to every arm.
- Dependency-parse negation scope: it would follow syntax rather than
  distance, but no source here compares it with a window (S3 uses cue
  detection and does not measure a parser), and it adds a parser dependency
  for 84 questions and 210 units. Not tried.
- Exact match against reference translations: rejected. It fails every
  faithful rewording (S1's note on partial deviations).

## Implementation decision

Two code rules, applied to every label. Then label fixes where a rule cannot
see the meaning.

1. A number is whole (`meaning.at`). A phrase still matches inside words, so
   stems such as `reimburs` work. An edge that is a digit takes the literal
   rule, `meaning.joins`. `31 days` is not in `131 days`, and `March 1` is not
   in `March 12`. Stage 2's `meaning.check` uses the same matcher.
2. Polarity (`korean.missed`): a window after S5 and terminators after S8,
   combined here as this page's own rule. A cue is `not`, `no`, `never`,
   `none`, `nothing`, `neither`, `nor`, `without`, `cannot`, `unless` or
   `n't`. Pseudo-cues negate nothing: `no more than`, `not only`,
   `without delay` and the like. A cue governs a phrase when it stands in the
   same clause, at most five words before it or three words after it.
   Clauses end at `, ; : . ! ?` (not inside a number), at a line break, and at
   `but`, `although`, `though`, `however`, `whereas`, `while`, `which` and
   `except`. A governed phrase does not meet its group in two cases:
   - the label has no cue at all (a positive fact);
   - the phrase is the positive wording in a group of negations (`prohibited`
     beside `never autoplay`).
   What a negation group negates (`sound` in `never autoplay with sound`) is
   not checked.
3. The asked part (`meaning.asked`). Groups that name what a question asks are
   met on its last line. A question that pastes something first asks it
   there, so pasted words never stand in for a lost target. Cycle 2 moved
   this off the line break.
4. Labels where a rule cannot see the meaning:
   - a limit bound to its amount (`within 7`, `7 or less`);
   - a rate bound to its unit (`one point` with `per euro`);
   - a change bound to its order (`112% to 101%`, `to 101% from 112%`);
   - an exclusivity bound to its object (`only on the Enterprise`);
   - the absence of each listed item (`no owner`, `or due date` after it);
   - three more units split. Bare cues and positive alternatives were removed
     from negation groups.

## Experiment (cycle 1)

- Every label on the English originals (`korean.baseline`, run by
  `korean.py --check`): none may fail.
- Round 2's reproductions and the round 1 cases (123 cases): each loss must
  fail and each faithful wording must pass.
- A CheckList directional sweep (S6) over every English original sentence and
  question that its label holds:
  - each number its label names, changed to 1n and n1;
  - each cue dropped, for a label that names a negation;
  - `not` inserted after each auxiliary, for a positive label.

  Every perturbation that still passes is read by hand.

## Result (cycle 1)

- Baseline: 84 pairs and 210 fact units, no failure.
- The 123 cases all come out as expected. Regression cases are in
  `tool/test_korean.py`. They cover round 2's reproductions of all six
  findings, and the matcher and polarity rules alone.
- The sweep:
  - No number perturbation of a unit's own number passes. The ones that
    pass change a number another unit of the same sentence checks.
  - No dropped cue passes in its own clause.
  - The inserted `not`s that pass are in another fact's clause, or are
    double negations (`not never`), or are ungrammatical questions.
  - Three own-clause cases remain, all window limits: `has not a plain-text
    version and an unsubscribe link`, `has not a lower-case URL ... and a
    unique title`, and `Support is not staffed from 08:00 to 18:00 CET on
    working days`, where the cue is seven words before `working day`. S5
    predicts both kinds: scope across coordination, and a long clause.

## Cycle 2

Round 3 found the class again, in rules cycle 1 had added. Each finding is a
failed hypothesis of cycle 1:

- The asked part on the last line assumed the translator keeps the line
  break. Merged onto one line, the pasted `Shared carts` stood in for a
  `private cart` again. Split over two lines, a faithful question failed.
- The fixed window assumed one proposition per clause. In `change that
  password at once and never use it`, the `never` of the next proposition
  denied `at once`. This is S5's own §4 false positive, and S8's terminators
  are its remedy.
- Exempting what a negation group negates assumed the object survives
  unchanged. `never autoplay without sound` kept every word. `have neither`
  matched without its owner.
- A bound amount assumed its unit or verb could not change: `within 7 years`,
  `lose one point per euro`. That is S1's SEN category without a negation
  word.
- Order phrases and dates assumed adjacency: `to just 101%` and `March 1st`
  failed.

Changes:

1. The asked part is the text after the question's last quotation mark, or
   else after its last pasted literal, or else its last line
   (`korean.asked_part`). Line breaks no longer move it.
2. `and` and `or` end a cue's scope, as S8's termination terms do. A cue no
   longer reaches the next proposition. Scope across coordination (`has not
   a version and a link`) was already a known miss.
3. A phrase's space matches any whitespace, a line break too (`meaning.at`
   returns spans).
4. Labels:
   - every phrase of a negation group carries what it negates (`no assigned
     owner`, not `have neither`; tailgating, marketing reach, office energy);
   - a negated relation is bound (`with sound`);
   - an amount carries its unit or its verb (`within 7 days`, `within 7.`;
     `earn one point`);
   - order phrases allow a modifier (`to just 101%`), dates the ordinal after
     the month (`March 1st`), and every change unit uses them.

### Experiment (cycle 2)

- The baseline and the 123 cases again. Round 3's reproductions are added to
  `tool/test_korean.py`.
- The directional sweep of cycle 1 again.
- A CheckList invariance sweep (S6): wordings that keep the meaning must still
  hold.
  - `, and` becomes ` and`;
  - every comma is dropped;
  - lines are merged, and the asked line is split;
  - `to N` becomes `to just N`;
  - a day-month date becomes `Month 1st`.
- A directional sweep of antonym swaps, which must fail:
  - earn/lose, with/without sound, rose/fell, within/after;
  - only/also, more/less than, over/under, at least/at most, before/after;
  - every/no, never/always, `is not`/`is`, `no `/`some `, delete/keep;
  - and others.

### Result (cycle 2)

- Baseline: no failure. The 123 cases come out as expected. With round 3's
  reproductions added, `tool/test_korean.py` and `tool/test_evidence.py`
  pass 134 tests, with 1 skipped.
- Invariance:
  - Every `, and`, merged-line, split-line and ordinal variant holds.
  - Three faithful sentences fail once every comma is dropped, as run-ons
    (`at once do not charge it`).
  - One literal fails because its pasted span lost a comma; that is correct.
  - `4.5 to just 1` is not a wording of a ratio.
- Directional: no swap passes inside the unit it changes. One kind is the
  exception: a self-contradicting change (`fell from 41,000 to 83,000`). The
  order of the numbers already fixes the direction, and the direction word is
  not required (contractor, free-to-paid, NRR, packaging, podcast). Every
  other swap that passes changes another unit's clause, or makes nonsense
  (`almost some`).

## Cycle 3 (an exception the owner approved)

Round 4 found five more P1s. PR 8's two cycles were spent, so the work
stopped and the owner was asked. The owner chose one more label fix
(2026-10-13). Every finding was again a hypothesis of the cycle before it
that did not hold:

- The last quotation mark was assumed to close the pasted part. A question
  that quotes its own term (`a "shared cart"`) lost its target. ASCII single
  quotes were not read as quotation marks at all.
- `within 7.` was given to accept the original's bare amount. Its last
  character is a point, so the number rule never ran, and `7.5` and `7,000`
  passed.
- `no page` was bound to the page but not to what the page does. `No page
  fails to answer` passed.
- Phrases matched any whitespace, but pseudo-cues needed one space.
  `no  more than` became a cue.
- `or` was made a terminator alongside `and`. Then `no captions or
  subtitles` let `subtitles` escape the cue.

Changes:

1. The pasted part is the first quotation or pasted literal, whichever
   starts first, with whatever starts inside it (`korean.asked_part`).
   - Double, typographic and single quotation marks count. A single mark
     counts only where no word joins it, so `What's` opens nothing.
   - A term the question quotes later stays in the asked part.
   - With nothing quoted or pasted, the asked part is the last line. On a
     single line, it is what follows a label's first sentence (`Prospect
     asks: Can we pay? ...`).
2. A phrase that ends in a point or comma after a digit ends the number
   there (`meaning.at`): `within 7.` is not in `within 7.5`.
3. A literal is as written apart from the width of a space
   (`meaning.found`). Pseudo-cues read any whitespace too.
4. `or` no longer ends a cue's scope; `and` still does.
5. `marketing-reach-2` binds the negation to the verb: `no page answers`.

### Experiment and result (cycle 3)

- Baseline: no failure. The 123 cases, round 4's 16 reproductions inverted
  to the wanted outcome, and the cycle 2 sweeps all come out as expected.
  Nine of round 4's reproductions are added to `tool/test_korean.py`.
- New invariance sweeps over every question with an asked part:
  - quotation marks made single;
  - every space doubled;
  - lines merged;
  - every quotation mark removed and the lines merged.

  Nothing fails, except one correct case: a code snippet whose own
  quotation marks changed loses its literal. Doubling every space in every
  English page fails no unit.
- `tool/test_korean.py` and `tool/test_evidence.py`: 143 tests pass, 1 is
  skipped.

## Unresolved

- A cue farther than the window, or across `and`, is not seen.
- A cue reaches across `or`, so in `not X or Y` a separate positive `Y`
  inside the window counts as denied.
- A sentence whose commas are all gone can count as a loss when a cue of
  the next proposition falls inside the window.
- A self-contradicting change word beside a correct number order passes.
- A literal's own polarity is not checked; a phrase group beside it is.
- These are measured on perturbations of the English originals, not on the
  translator's real output. Each round has found new perturbations of the
  same kind. A substring checker has no complete answer to them, and the
  paid run's per-unit outcomes still need a person to read them. Each
  outcome names its English, so a person can.
