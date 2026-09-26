---
scope: operator
severity: landmine
repeat: rule
triggers: ["[\\s\\S]"]
slots: []
enforce:
  pretooluse: english_progress.py
sources: []
sources_withheld: true
links: [ask-with-arrow-key-options, hooks-fail-open]
---

# What the agent writes is English. What the person reads is mirrored

Rule. Tool descriptions, progress reports and chat replies are written in
English, and so is everything else the agent writes for the agent: comments,
docstrings, test names, commit messages. What a person reads stays Korean — a
hook's refusal, `systemMessage`, UI labels, the prose in `docs/`, a pull
request (translated on the way up). Wiki pages — `operator/`, `craft/`,
`.wiki/` — are English; only `triggers` keep the person's language. Korean
survives in English text only where it names a Korean thing — the glossary
holds that list, and a word the translator refuses to render is exactly a word
this rule may not deny.

What goes wrong. Thinking in one language and writing in another costs the
agent accuracy on every turn, and this repository spent a long time paying it:
every surface the agent touched was Korean. The person still reads Korean —
that is what the mirror is for — but the reading now happens after the work,
not during it.

## Why this page is layer 4

| Layer | Why it does not hold |
| --- | --- |
| 1 (`permissions.deny`) | The violation is not shaped like a command. "This string has Hangul left in it" is a computation |
| 3 (a skill) | It is a habit on every call, not a procedure. There is no step to wrap |

`tool/english_progress.py` reads the `description` of `Bash`, `Agent` and
`Task` and nothing else. Not prompts, not file contents — only what lands on
the person's screen exactly as written. Anything goes wrong, it passes.
Enforcement that stops the work is enforcement the person turns off.

## The refusal stays Korean

The rule is about what the agent writes. The refusal is read by the person, so
it is written in Korean, and so is `systemMessage`. The two languages here are
not a compromise; they are the two readers.

## Comments and docstrings are English too

The hook does not read them — it only sees a `description` — but the rule is
not the hook. Everything the agent writes for the agent is English: comments,
docstrings, test names, commit messages. An earlier draft of this page carved
comments out as "each repository's business", and what that produced was one
file in English sitting next to one in Korean, in the same change, by the same
hand. A boundary nobody can state in a sentence is not a boundary.

The line that does hold is about the reader. Anything a person reads stays
Korean: the refusal above, `systemMessage`, UI labels, the prose in `docs/`
and on these pages until the phase that rewrites them gets there.

## What it does not block

- A description with no Korean left outside the glossary passes
- A call with no description passes
- Comments are out of the hook's reach, which is why they are written down
  here as a rule rather than left to a check

## Why this rides on every utterance

The trigger used to be words such as `한국어` or `영어`. The rule then reached
only the turns that talked about language. On 2026-09-23 an ai-nara-shop
session analysing v9, v19 and v24 had no such words in its two utterances.
It answered in Korean all the way through, and its tool descriptions were in
English because the hook checks only those. A reply is written on every turn,
so the rule is sent on every turn too, the same as
[[ask-with-arrow-key-options]].

## The order this arrived in

The mirror had to run first. Flipping the rule before a person could read
Korean anywhere would have left them watching English with no way back, and
the plan that carried this change wrote that down as its one ordering rule.
