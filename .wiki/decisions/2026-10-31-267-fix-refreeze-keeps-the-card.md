---
scope: project
severity: preference
triggers: []
domain: ''
title: "While the fixed test change approval is running, the card remains in a single line, the review does not stop the card, and the project can be changed."
pr: 267
merged: 2026-10-31
branch: "fix/refreeze-keeps-the-card"
---

# While the fixed test change approval is running, the card remains in a single line, the review does not stop the card, and the project can be changed.

What. [Fixed test change approval] While this is running, the work tree does not appear split in two. In ai-coding-wiki `preserve-repository-boundaries-3578a758`(ai-coding-wiki#162), since the work tree is the main checkout itself, the checkout stood at `40d0788` without a branch for the few minutes the fixed test was running on the code before the change. Rails showed this as a second task without a specification. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #267 · `fix/refreeze-keeps-the-card`
