---
scope: craft
severity: contract
repeat: rule
triggers: ["테스트를? (돌|실행)", "pytest", "전체를? 돌리", "다 돌리", "라이브를? (돌|실행)", "회차를? (돌|실행)", "게이트", "검증", "확인해 ?보"]
slots: []
sources: []
sources_withheld: true
links: [diagnose-from-what-ran, pick-up-async-results, do-the-whole-instruction]
---

# Measure narrow, go wide at the end — not everything on every repair

Rule. While fixing, run **only what the change touches**. The full suite and
the full live run happen once, just before a commit, a review or a PR.

| When | What |
| --- | --- |
| Fixing one judgement | That one test file |
| Closing out a domain | That domain's directory |
| Before a commit, a review round or a PR | The whole gate |
| A change whose main evidence is live | One live run |

Do not run a whole scenario for one measurement. Add instrumentation if it is
needed, but drive only the shortest path that produces the value and stop when
it does. Running to the end does not make the answer more accurate.

Catching with the whole what the local would have caught is not safety, it is
cost — and a quiet one. The gate stays green, so nothing anywhere signals that
something is wrong. It shows up a day later as "why has nothing landed yet".

Two exceptions. A failure with an unknown cause has no basis for narrowing, so
run everything. And a change to a shared file means looking at every place
that uses it.

What goes wrong. The time spent verifying passes the time spent repairing, and
the backlog stays exactly where it was.

## Before spending the time, ask what the time buys

The same scale sits in other places. What this rule weighs is one thing: does
this run answer anything new?

- Anything untouched since the last run is not run again.
- A live run that has already given its answer is not driven to the end.
- The same verification is not re-run while waiting for a review round —
  waiting is held by [[pick-up-async-results]].

The depth belongs to the target repository's `.wiki/gates.md`. Which commands
are gates, and how long each takes, differ per repository.
