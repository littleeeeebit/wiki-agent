---
scope: craft
severity: landmine
repeat: rule
triggers: ["이어서 (하|진행)", "계속 (하|진행)", "이어서 하고 있는가", "진행중인가", "돌고 있는가", "하겠습니다", "Stop 훅", "stop hook"]
slots: []
sources: []
sources_withheld: true
links: [do-the-whole-instruction, hooks-fail-open, pick-up-async-results]
---

# Having written that it will continue, continue in that response

Rule. A turn that says it will do something right now, in this response, does
not end without a tool call. If user input is genuinely needed, ask in one
line what is needed and end there. Do not leave the promise and stop.

What goes wrong. **Work believed to be running is worse than work not run.**
The user ends up making that judgement, and the automation is worth nothing.

## Layer 4 — `tool/declared_continuation.py`

A Stop hook reads the last assistant message and reverts with
`{"decision": "block"}` only when every condition holds.

| What it reads | When it reverts |
| --- | --- |
| The promise | `이어서`·`계속`·`바로`·`지금`·`곧` with a `-겠습니다` ending, or a sentence opening `I'll`, `I will`, `Let me` |
| Deferral | Only when the same sentence has no `끝나면`·`결과가`·`알림이`·`다음에`, and no `once`, `after`, `when`, `waiting for` |
| Tool calls | Only when that response made none |
| How it ends | Only when it does not end on a question (`?`) |

A true `stop_hook_active` passes unconditionally. The sentence is still there
after one revert, so without that it could revert forever.

## Why the judgement has to stay narrow

`hooks-fail-open` makes the same argument here: enforcement that stops the
work is enforcement the user turns off entirely.

## A turn that waits writes down what it is waiting for

Instead of being reverted, write it: what is being waited on, how long it
takes, what happens when it lands. `pick-up-async-results` is that half.
