---
scope: craft
severity: contract
repeat: rule
triggers: ["주석", "docstring", "독스트링", "line ?length", "줄 ?길이", "리팩터|리팩토링"]
slots: []
sources: []
sources_withheld: true
links: [diagnose-from-what-ran, do-the-whole-instruction]
---

# Comments carry the why, not the history

Rule. Comments and docstrings hold only what cannot be learned by reading the
code — why this structure, what was deliberately not done, which external
constraint forced this shape. What the code already says is fixed with a name
and a structure, not with a comment. Dates, PR numbers, branch names and
worklogs go to `.wiki/decisions/` and to commits. A test inverts this: its
comment says what broke, how, and how the test holds that ground. Fixing the
code and its comment is one change, and a cause goes into a comment only once
it is confirmed. And lines break at meaning — at a sentence, clause or phrase
boundary, never leaving `the`, `of`, `and`, `to` or `is` stranded at a line's
end. A length limit exists to make reading easier; it is not a number to hit.

What separates them is reason against history. Why this structure is visible
whenever the code changes, so it lives next to the code. When, on which
branch, what was fixed does not change when the code does, so it ages alone.
That belongs to `.wiki/decisions/` and to commits.

What goes wrong. One of two things. Remove the reason and the next person
remakes the same decision, or reverses it. Accumulate the history and the
comment confidently points at something untrue, which is worse than no comment.

## What stays and what goes

| Keep | Remove or move |
| --- | --- |
| Design intent — why this structure was chosen | A sentence that restates the code |
| A counter-intuitive constraint — why this order, this type | Dates, PR numbers, branch names |
| How an external system behaves — it answers like this | A worklog: "it used to be A, changed to B" |
| What was deliberately skipped and why (including a `ponytail:` marker) | Debugging recollections with no reproduction |
| The part of a regression's cause that explains the code as it stands | A second copy of a story the wiki already holds |

The first row of the remove column is the common one. When a comment restates
what the code says, the fix is the name and the structure, not a shorter
comment. One good name removes three lines of comment.

Reducing the quantity is not the goal. The goal is density, not brevity — if
every line left says something the code cannot, length is fine.

## Lines break at meaning

Three things to hold.

- Break at a sentence, clause or phrase boundary. Do not leave an article, a
  preposition, a conjunction or an auxiliary stranded at the end of a line;
  `the`, `of`, `and`, `to`, `is` belong with what follows them.
- Do not spread across lines what fits naturally on one. A modifier added to
  reach a second line is noise, not information.
- Two points mean two paragraphs. One blank comment line says "a different
  thing starts here".

## Test comments invert the judgement

The history removed from ordinary code is an asset in a test. A regression
test is worth not what it checks but why it is here — what broke once — and
removing that leads the next person to ask "why is this assertion so tight"
and loosen it.

So a test does not say what it tests; the name and the assertion already say
that. What it says is what broke, how, and how this test holds that ground.
Written with the reproduction conditions, the diagnosis is half done the
moment it turns red.

## Fixing the code and its comment is one change

When a refactor changes the meaning, look at the comment too. Move a function
and rename it while leaving the comment behind, and that comment now describes
behaviour that does not exist. Do not count the places to fix with the
comments left out — the same face as [[do-the-whole-instruction]].

When writing a cause into a comment, write only what was confirmed. A
plausible guess in a comment is read by the next person as established fact —
[[diagnose-from-what-ran]].

## What the check catches and what it cannot

`tool/lint.py::broken_wraps` points at a break that split a phrase. It counts
lines ending on a word that belongs with the next one, across this
repository's `tool/*.py` and the page prose. `test_lint.py` keeps that check
actually turning red.

The rest has no check. Whether a comment restates the code, whether the
regression cause left behind explains the implementation as it stands, are
judgements, and mechanising a judgement means false positives stopping the
work. So this page stays layer-5 prose and rides only when an utterance
catches it.

It does not ride on every moment of writing code. Catching that moment as a
trigger would mean riding on every utterance, and that is the problem this
wiki was built for. Instead `lint` catches it afterwards, and if the findings
are counted again and have grown, that is when the ladder goes up to
`PostToolUse`.

Emphasis in a document is the same kind of judgement. What deserves bold is
taste, but paragraph labels, two in one line and the ratio are counted by a
machine — [[emphasis-is-scarce]] holds where that line was drawn.
