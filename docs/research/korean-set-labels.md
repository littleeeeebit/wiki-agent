# Meaning labels for the Korean set

Reliability PR 9 counts a Korean translation as a loss when its normalized
English fails the set's labels. The labels must reject a translation that
changed a number, flipped a negation or lost a condition. They must accept the
common faithful wordings. This page is PR 8's research step for the set review
(#135). The same P1 class came back in rounds 1 and 2. This is research and fix
cycle 1 of at most 2.

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
| S5 | Chapman et al. 2001, NegEx ([J. Biomed. Inform. 34(5)](https://doi.org/10.1006/jbin.2001.1029)) | Rule-based negation. A trigger negates terms within a window of about five words. Pseudo-triggers (`no increase`, `not only`) negate nothing. Terminators (`but`, `however`) end the scope. Post-triggers negate the terms before them. The method is precise in clinical text and misses long-range scope. |
| S6 | [Ribeiro et al. 2020, CheckList](https://arxiv.org/abs/2005.04118) | Behavioural testing with three test types. Minimum functionality tests check targeted cases. Invariance tests apply perturbations that must not change the result. Directional tests apply perturbations that must change it. Templates and perturbations find failures that held-out accuracy hides. |
| S7 | Translation QA tools: Xbench's numeric mismatch check and Trados's number verifier (vendor documentation) | Commercial QA matches numbers as whole tokens and normalizes their formats. Boundary handling is the documented source of false matches. |

## Core theory

S1 and S4 define the losses that matter as a number change or a polarity change
(interpretation: the set's losses match the WMT critical categories). S2 and S7
show that numbers need a token-level check, not a substring check. S3 and S5
show that polarity is a property of a cue and its scope, not of the words
present. A check that only finds a phrase cannot tell `has alt text` from
`does not have alt text`. S6 supplies the test method: directional
perturbations that must flip the outcome, and faithful rewordings that must not.

## Applicability

The checker runs on short English rule and fact sentences, written by the
translator from a known Korean source. The negation patterns are those of S5's
setting: a cue a few words before or after its term, with clause punctuation
as the boundary. Numbers in the set are integers, decimals, comma thousands,
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
- Dependency-parse negation scope: more accurate than a window (S3), but it
  needs a parser dependency for 84 questions and 210 units. A window with
  terminators covers the set's sentences (Result).
- Exact match against reference translations: rejected. It fails every
  faithful rewording (S1's note on partial deviations).

## Implementation decision

Two code rules, applied to every label. Then label fixes where a rule cannot
see the meaning.

1. A number is whole (`meaning.at`). A phrase still matches inside words, so
   stems such as `reimburs` work. An edge that is a digit takes the literal
   rule, `meaning.joins`. `31 days` is not in `131 days`, and `March 1` is not
   in `March 12`. Stage 2's `meaning.check` uses the same matcher.
2. Polarity, after S5 (`korean.missed`). A cue is `not`, `no`, `never`,
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
   there, so pasted words never stand in for a lost target.
4. Labels where a rule cannot see the meaning:
   - a limit bound to its amount (`within 7`, `7 or less`);
   - a rate bound to its unit (`one point` with `per euro`);
   - a change bound to its order (`112% to 101%`, `to 101% from 112%`);
   - an exclusivity bound to its object (`only on the Enterprise`);
   - the absence of each listed item (`no owner`, `or due date` after it);
   - three more units split. Bare cues and positive alternatives were removed
     from negation groups.

## Experiment

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

## Result

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

## Unresolved

- A cue farther than the window, or across coordination, is not seen.
- A legitimate negation of another fact in the same clause, within the
  window, counts a faithful sentence as a loss. Neither the baseline nor the
  cases show one. The paid run's per-unit outcomes name any that occur, so a
  person can read them.
- A literal's own polarity is not checked; a phrase group beside it is.
