---
scope: craft
severity: landmine
repeat: rule
triggers: ["테스트를? (돌|실행)", "pytest", "전체를? 돌리", "다 돌리", "라이브를? (돌|실행)", "회차를? (돌|실행)", "게이트", "검증", "확인해 ?보", "리뷰\\s*루프", "review\\s*loop", "머지\\s*허용"]
slots: []
sources: []
sources_withheld: true
links: [diagnose-from-what-ran, pick-up-async-results, do-the-whole-instruction, codex-review-loop]
---

# Measure narrow, go wide once at the end — not everything on every repair

Rule. During implementation and review repairs, run only checks that answer
the changed behavior. One owner runs final verification after `머지 허용`
and before merge; outside a review loop, on the final candidate. A repository
with configured, available hosted CI delegates the full suite there. Private
repositories and repositories without CI use one bounded local final run.
Do not add a local full run to CI, or start CI because a local run failed.
An unknown failure requires diagnosis and a stated reason to widen checks;
it does not authorize a surprise local full suite. Reuse passing evidence for
the same code and conditions. A live scenario stops when it has its answer.

Why. In one review loop (Jev stage 5, six rounds) the whole gate — 780
tests, 7.5 to 10 minutes — ran before every round, while the one file the
fixes touched ran in 15 seconds and caught every regression the rounds had.
About an hour of the loop went to a suite that answered nothing new. The user
set this rule at the top.

| When | What |
| --- | --- |
| Fixing one judgement | That one test file |
| Closing out a domain | That domain's directory |
| A review round, a commit, a push | What the change touches |
| After `머지 허용`, before merge | One final owner; hosted CI or bounded local work |
| No CI or a private repository | One local final run, low concurrency, never one per round |
| CI has already been requested | Wait or diagnose; never duplicate it locally |
| A change whose main evidence is live | One live run |

Do not run a whole scenario for one measurement. Add instrumentation if it is
needed, but drive only the shortest path that produces the value and stop when
it does. Running to the end does not make the answer more accurate.

Catching with the whole what the local would have caught is not safety, it is
cost — and a quiet one. The gate stays green, so nothing anywhere signals that
something is wrong. It shows up a day later as "why has nothing landed yet".

Shared-file changes include their consumers. If the mapping is unknown, name
the missing coverage and defer the broad check to the final owner. Do not
pretend a narrow check proves all consumers, and do not run the full suite at
every repair. A failed, timed-out or interrupted attempt remains recorded;
one successful retry does not erase the failure or establish its cause.

What goes wrong. The time spent verifying passes the time spent repairing, and
the backlog stays exactly where it was.

## Before spending the time, ask what the time buys

The same scale sits in other places. What this rule weighs is one thing: does
this run answer anything new?

- Anything untouched since the last run is not run again.
- A live run that has already given its answer is not driven to the end.
- The same verification is not re-run while waiting for a review round —
  waiting is held by [[pick-up-async-results]].
- Session restart, a new agent and a Merge click are not reasons to rerun.
- CI failure and CI absence are different: decide the owner before execution
  and keep it for the attempt. Preparation trouble stops pending, not in an
  automatic implementation-repair loop.
- Verification cost includes elapsed time, repeated execution and disk pressure.
  Improving the procedure includes removing the previous execution path.

The depth belongs to the target repository's `.wiki/gates.md`. Which commands
are gates, and how long each takes, differ per repository.
