---
scope: operator
severity: contract
repeat: rule
triggers: ["(?s).+"]
slots: []
enforce:
  pretooluse: host_boundary.py
sources: []
sources_withheld: true
links: [codex-review-loop, do-the-whole-instruction]
---

# Do not multiply subagents

Rule. Unless the user explicitly asked, never create, call, resume or delegate
a subagent, subsession or child worktree agent. A review procedure, available
tool or speed benefit is not permission; permission for one task does not
carry into the next. Independent review and implementation use separate cells.
Reuse the user's reviewer and preserve it during subsession cleanup. Code and
review go to `sol`; re-analysis, literature search and stuck designs go to
`astra`. If the assigned model is uncertain, ask before sending. Send review
only after every live run finishes. In wiki-agent the application owns review
dispatch and task metadata; use its review loop control. Never discover or
invoke another desktop host's CLI to list, create or message reviewers.

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

In wiki-agent the application selects and owns the independent reviewer.
Use the review loop control; the implementation agent never lists external
terminals or sends a review itself. The app's loop settings identify the
review model. In another host, use that host's native session information.
If the assigned reviewer cannot be identified, ask before dispatch —
[[ask-with-arrow-key-options]]. Never substitute another application's CLI.

## With several live runs, send after all of them

Do not prepare a round each time one run finishes. An intermittent failure
says nothing after a single run — if it appears once in five, one green is not
evidence of a repair, it is the run that missed — and an instruction written
on that basis hands the reviewer grounds it does not have. Finish the runs,
write down every result, then send.
