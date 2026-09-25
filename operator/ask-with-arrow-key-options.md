---
scope: operator
severity: contract
repeat: rule
triggers: ["[\\s\\S]"]
slots: []
enforce:
  deny: ["request_user_input_async", "functions.request_user_input_async"]
sources: []
sources_withheld: true
links: [english-progress]
---

# A decision the user owns is asked as options, not as prose

Rule. Where the user's judgement is needed, use the current host's tool for
asking with options. On Claude Code that is `AskUserQuestion`; on Codex it is
`request_user_input`, when the session offers it and permits it for this use.
**Calling Codex's `request_user_input_async` is forbidden outright.** A call
with no options is not an exception. The user set recurrence severity at P0.

## The tool depends on where this is running

| Environment | Tool | What to check |
| --- | --- | --- |
| Claude Code, interactive | built-in `AskUserQuestion` | The spec's `questions`, `options`, `multiSelect`. Codex experiment settings are irrelevant |
| Codex | `request_user_input` | Whether it is offered and permitted for this mode and use. `header`, `id`, `question`, and each option's `label` and `description` |
| Codex, asynchronous | `request_user_input_async` | Forbidden outright, and so is `functions.request_user_input_async` |

- Do not apply a Codex tool or setting to Claude. The two schemas are not
  copied across either.
- Codex support in the default mode depends on the installed version and the
  host. Where `default_mode_request_user_input` exists, check that experiment
  too. Enabling it is not the same as having seen the UI work. The session's
  own tool list and limits come first.
- If the tool is missing or forbidden for this use, say what the limit is and
  ask the way the higher instructions allow. Do not describe prose or an
  asynchronous question as the same UI.
- Put the recommendation first and describe its consequence and its cost.
  Only an answer actually submitted counts; an acknowledgement or a
  preselected item is not one.
- When the default is obvious, decide and carry on. Ask only where a different
  reading changes the work. Finish whatever does not depend on the answer first.
- Having asked the wrong way, ask the same question again with the right tool.

## Why this rides on every utterance

The judgement appears while the agent is writing its answer, so waiting for an
"options" word in the user's message is already too late. A short form of the
rule now goes out on every non-empty utterance. That is not a mechanism that
forces the UI to be called, and it is not proof that it was. The actual call
and the user's reply are confirmed separately.

`enforce.deny` and `codex_pretool.py` block both asynchronous names.

What goes wrong. The user restates the format and makes the same decision
twice. An option's label and description are read by the person, so they stay
Korean — [[english-progress]] holds that boundary.
