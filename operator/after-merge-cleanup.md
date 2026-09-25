---
scope: operator
severity: contract
repeat: rule
triggers: ["머지", "merge", "브랜치.*정리", "정리.*브랜치"]
slots: [server_stop, scratch_dirs]
sources: []
sources_withheld: true
links: [do-the-whole-instruction, run-inside-this-session]
---

# Cleanup after a merge — before being told to

Rule. A request to merge includes the cleanup that follows it. After
confirming the merge actually landed, and before starting the next piece of
work, run the skill `after-merge` all the way through. Do not seek separate
approval for the cleanup and do not push it to the next turn. In this
repository the server stops with `{server_stop}` — only what this session
started, told apart by command line ([[run-inside-this-session]]) — and the
scratch to clear is `{scratch_dirs}`. Where the skill is not installed, its
steps are in the hub wiki's `skills/after-merge/SKILL.md`.

What goes wrong. Dead branches pile up, a server sits holding the GPU, and the
next session reads stale scratch as evidence.

Do not treat the page existing as the step being done. Put a real merge
request through the injector and confirm this rule lands in the body.
