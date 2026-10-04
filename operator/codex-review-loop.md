---
scope: operator
severity: landmine
repeat: rule
triggers: ["리뷰\\s*루프", "review\\s*loop", "codex.{0,12}(리뷰|보내|돌려)", "라운드\\s*\\d+\\s*(를|을)?\\s*(보내|전송)"]
slots: [review_dir, gate_cmd, live_cmd]
sources: []
sources_withheld: true
links: [pick-up-async-results, ask-with-arrow-key-options, agent-delegation, gate-the-exit-not-the-callers, measure-after-the-last-change, verify-narrow-then-wide, cloud-local-review]
---

# The Codex review loop — the application owns dispatch

Rule. Use `review-loop`. In app-managed wiki-agent sessions, the review loop control asks the server
to attach an existing task PR, write instructions, dispatch its independent
reviewer and persist the result. The implementation agent never edits raw
metadata or contacts an external terminal. Review authorization does not
permit another implementation agent — [[agent-delegation]]. In a host that
explicitly supplies a manual review cell, write
`{review_dir}/<topic>-round-<n>.md`, arm the result watch, then send one native
instruction to read it. Read `<topic>-round-<n>-result.md`, not terminal output
— [[pick-up-async-results]]. Repair rounds run scoped checks and carry actual
live evidence (`{live_cmd}`). Commit and push the fixes. After `머지 허용`,
run `{gate_cmd}` once on that head before a requested merge —
[[verify-narrow-then-wide]]. The procedure is in `skills/review-loop/SKILL.md`.

In the managed app, start the loop once per PR. The server waits for completed
repairs and publication, advances subsequent rounds automatically, and resumes
interrupted execution on restart. Read a stopped task's persisted reason;
never assume it means waiting for another button click, and never store manual
per-round clicking as a standing instruction.

Cloud implementations additionally follow [[cloud-local-review]]: a separate
local reviewer checks actual API/browser evidence, and failures return to
cloud. The ordinary local implementation workflow stays as above.

What goes wrong. The round disappears quietly. Worse is
**a review that looks like it happened and did not** — a session that never
read the instruction asks "which file?", or reviews whatever it had open and
hands that back.

The procedure — what an instruction must contain, the grades, reading the
result, sorting findings by family when the rounds stop shrinking, and when it
ends — lives in the skill. This page keeps the rule and this repository's
values, which a skill cannot carry: it is the same file everywhere.
