---
scope: craft
severity: contract
repeat: rule
triggers:
  - "머지했다"
  - "머지 완료"
  - "merge 완료"
  - "브랜치.{0,6}정리"
  - "브랜치를 (모두|전부)?\\s*지워"
  - "복귀하고"
  - "잔여물"
  - '(그리고|또한|그 ?다음|아울러)[ ,]{0,2}[^\n?!]{0,80}(\?|해라|해줘|하라|알려줘|어떻게 생각)'
slots: []
sources: []
sources_withheld: true
links: [after-merge-cleanup, declared-continuation, comments-carry-why]
---

# Do the whole instruction — do not make them ask for the rest

Rule. When an instruction has several items, do all of them. If one is
blocked, finish the others and name what was blocked and why. Never narrow the
scope quietly. A sentence that opens a second thing with `그리고`, `또한` or
`그 다음` is several items too; a task and a question in one utterance are both
finished in that turn, the answer in one line first when the task is long.
Stopping is right only for a judgement the user owns, an action that is hard
to reverse or goes outward (push, merge, deploy, delete), or a place where any
assumption is unsafe; anything else continues, saying what was assumed. If
there is something to ask, ask it in that turn, as options — never write that
you will ask and then not ask.

Stopping halfway makes the user find the remainder and ask again, which means
the person who gave the instruction is now the one inspecting the result.

What goes wrong. The round trips double. And because the user has to check
"what got left out this time" on every turn, the automation stops being worth
having.

## One utterance asking two things gets both answered in that turn

An instruction is not multi-item only when it is a list. A sentence that opens
a second thing with `그리고`, `또한` or `그 다음` is multi-item too. The
common shape is one task and one question.

Do not do the task and leave the question. Do not answer the question and push
the task to the next turn. Both finish in the same turn. When the task is
long, give the answer in one line first and then continue — an answer placed
after the work makes the user read the turn and ask again.

## The neighbouring failure — stopping where stopping is wrong

Reporting and stopping with work still to do is the same failure wearing
another face. A report does not stand in for progress.

## What gets missed when counting the places to fix — comments

Changing a function and the comment that described it are one change. Fix the
code and leave the comment and that comment is now a sentence describing
behaviour that no longer exists, which the next person reads as fact. What to
keep and what to remove is held by [[comments-carry-why]].

## What has to be told apart

There are places where stopping is correct.

- A judgement only the user can make → [[ask-with-arrow-key-options]]
- An action that is hard to reverse or goes outward (push, merge, deploy, delete)
- A place where proceeding on any assumption is unsafe, or makes the result
  worthless if the assumption is wrong

Anything else continues. Decide the rest, and say what was assumed.

All three ask the same question: is the next step irreversible, or is it not
mine? Running a gate, reading a check, digging through a log, doing one more
reproduction run — all reversible, all mine. None of them is a place to stop.

Writing that it will ask and then not asking is the worst form of this. No
work was done and no question was left, so the user cannot even tell what is
being waited on. If there is something to ask, **ask in that turn** — as
options, and with whatever does not depend on the answer already finished.

## A hook reverts this

`tool/declared_continuation.py` blocks the shape as a Stop hook. If the last
message ends on a promise for this turn and not one tool was called, it
reverts, and the reason it gives back is the three places above, verbatim.

It reads the Korean endings `-겠습니다`, `-겠다`, `-겠음` and the English
openings that carry the same promise, and it catches a promise to ask
separately so it can give a different reason. A short list always has an
outside, and that outside is the next incident — [[hooks-fail-open]] wrote the
same sentence about its own scope.
