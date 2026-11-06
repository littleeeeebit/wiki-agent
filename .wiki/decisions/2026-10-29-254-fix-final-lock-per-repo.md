---
scope: project
severity: preference
triggers: []
domain: ''
title: "Even if the final verification of another repository is running, the final verification of this repository does not stop."
pr: 254
merged: 2026-10-29
branch: "fix/final-lock-per-repo"
---

# Even if the final verification of another repository is running, the final verification of this repository does not stop.

What. If the final verification is running in repository A, the final verification of repository B stops with "Stopped — Preparing local verification · Another final verification is in progress. Did not execute redundantly.".

Why. There were two layers of locks for the final verification. `final()` holds `wiki-verification.lock` in the repository's git common folder throughout the verification. This lock is separate for each repository. `local_result()` additionally held one more `%TEMP%\wiki-agent-final.lock` during the local check. This lock is one per machine. Running verification twice in the same repository is already blocked by the first lock alone. The only thing the second lock did was block verification of other repositories.

Source. PR #254 · `fix/final-lock-per-repo`
