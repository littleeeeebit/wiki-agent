---
scope: operator
severity: landmine
repeat: rule
triggers: ["리뷰\\s*루프", "review\\s*loop", "codex.{0,12}(리뷰|보내|돌려)", "라운드\\s*\\d+\\s*(를|을)?\\s*(보내|전송)"]
slots: [review_dir, gate_cmd, live_cmd]
sources: []
sources_withheld: true
links: [pick-up-async-results, ask-with-arrow-key-options, agent-delegation, gate-the-exit-not-the-callers, measure-after-the-last-change]
---

# The Codex review loop — files carry it, the terminal is a doorbell

Rule. Reviews pass through files, one round at a time with the skill
`review-loop`. The reviewer is the session already assigned; a request for an
independent review does not widen into spawning another implementation agent
— [[agent-delegation]]. The instruction goes in
`{review_dir}/<topic>-round-<n>.md`, and the terminal receives one sentence:
`'read that file and review it'`. The reviewer writes the result to
`<topic>-round-<n>-result.md`. Do not scrape the result off the terminal. Arm
the watch before sending, and do not end the turn between the two —
[[pick-up-async-results]]. Every round runs the offline gate `{gate_cmd}`,
carries the live run's result where there is one (`{live_cmd}`), and is
committed and pushed. Where the skill is not installed, its steps are in the
hub wiki's `skills/review-loop/SKILL.md`.

What goes wrong. The round disappears quietly. Worse is
**a review that looks like it happened and did not** — a session that never
read the instruction asks "which file?", or reviews whatever it had open and
hands that back.

The procedure — what an instruction must contain, the grades, reading the
result, sorting findings by family when the rounds stop shrinking, and when it
ends — lives in the skill. This page keeps the rule and this repository's
values, which a skill cannot carry: it is the same file everywhere.
