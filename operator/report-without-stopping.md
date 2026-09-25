---
scope: operator
severity: landmine
repeat: rule
triggers: ["멈추지\\s*마", "왜\\s*(자꾸\\s*)?멈추", "돌리고\\s*있", "계속\\s*(해|진행)", "다음은", "이어서", "쭉\\s*진행"]
slots: []
sources: []
sources_withheld: true
links: [english-progress, pick-up-async-results, ask-with-arrow-key-options]
---

# Report, but do not end the turn

Rule. **Having written down what comes next, start it in the same response.**
A progress report is something done while continuing, not instead of it.
Ending the turn is right in three places only: a decision only the user can
make is left, pushing, opening a PR and merging among them; everything asked
for is finished; or it is blocked, and what blocked it is written down by name
and reason. Anything else, call one more tool — a response ending "next I will
…" is itself the violation. Right after a merge or a sent round, look for the
next item in the queue. Before stopping to wait for approval, finish
everything that does not need it.

There are only three places where ending the turn is right.

| When ending is fine | Why |
| --- | --- |
| A decision only the user can make is left | Either choice sends the work somewhere different — [[ask-with-arrow-key-options]] |
| Everything asked for is finished | There is no tool left to call |
| It is blocked | Write down what blocked it, by name and reason |

Anything else, call one more tool. A response ending "next I will …" is itself
the signal of a violation: being able to write that sentence means the next
tool call is already known, and if it is known it can be made.

What goes wrong. The user becomes the continue button. They step away
believing it runs on its own, nothing is running, and they are also the one
who discovers that. It is the same shape of the automation being worth nothing
as [[pick-up-async-results]].

## "Share the result" does not mean "stop"

Both rules hold at once. Running without reporting is a violation, and
reporting and stopping is a violation. There is one answer: report and carry on.

## Right after a merge or a sent round is the dangerous spot

The question there is not "is this worth reporting" but "is there a next item
in the queue". If there is, write the report and call the next tool in the
same response.

## Do not confuse this with what needs approval

Pushing, opening a PR and merging all need to be asked for. That falls under
the first row above, so it does not conflict with this rule. Even then, finish
everything that does not need approval before writing "waiting for approval"
and stopping.
