---
scope: operator
severity: contract
repeat: rule
triggers: ["(?s).+"]
slots: []
sources: []
sources_withheld: true
links: [codex-review-loop, do-the-whole-instruction]
---

# Do not multiply subagents

Rule. Unless the user asked for it in so many words, never create, call,
resume or re-delegate a subagent or subsession such as `spawn_agent`. Creating
a child worktree and attaching an agent to it is the same thing by another
route. A review procedure in a document, the existence of a tool and a
judgement that it would be faster are none of them permission, and permission
granted for one task does not carry into the next. Separate work and review
cells standing side by side in one workspace are not what this forbids:
implementation and independent review happen in different Codex cells, and a
cell's own checks are not an independent review. Keep the review cell the user
made, reuse an existing one before opening another, and do not close it when
asked to clean up subsessions. Code and code review go to `sol`; re-analysis,
literature search and a stuck design go to `astra`. If which cell is `sol` is
not certain, ask before sending. With several live runs going, send the round
after all of them have finished.

- Implementation and independent review happen in different Codex cells. A
  cell's own checks are not an independent review. Keep the review cell the
  user made, and reuse an existing one before opening another.
- Opening a cell the work needs is allowed. That allowance does not widen into
  permission to spawn subagents, and cells are not multiplied for their own sake.
- A request to clean up subsessions does not close an independent review cell.
  Ending a review does not remove a cell the user set up.
- A review procedure in a document, the existence of a tool, and a judgement
  that it would be faster are none of them permission. Permission granted for
  one task does not carry into the next.

What goes wrong. Sessions that need context handed in and results carried back
cost more than they return, and responsibility for the work spreads out until
nobody holds it.

This rule does not depend on the kind of work, so it is injected on every
non-empty utterance. Injection is not a blocker. Permission is judged from
what the user actually asked for.

## With several cells open, which one is also decided

Not multiplying cells and sending work to the right one are different rules.

| Work | Where |
| --- | --- |
| Writing code · reviewing code | `sol` |
| Hard problems — re-analysis, literature search, handing over a stuck design | `astra` |

Handing a code review to `astra` is a waste of an expensive model.

When the cell list does not show the model, read the preview in
`orca terminal list`. The model is chosen inside the Codex session rather than
as a cell option, so a title alone does not tell them apart. If which cell is
`sol` is not certain, ask before sending the round —
[[ask-with-arrow-key-options]]. A round sent to the wrong cell spends that
model's time and leaves the review loop with nothing.

## With several live runs, send after all of them

Do not prepare a round each time one run finishes. An intermittent failure
says nothing after a single run — if it appears once in five, one green is not
evidence of a repair, it is the run that missed — and an instruction written
on that basis hands the reviewer grounds it does not have. Finish the runs,
write down every result, then send.
