---
scope: project
severity: preference
triggers: []
domain: ''
title: "Search: Count the chunk submitted with the auxiliary question only once, and do not set a floor for small rounds."
pr: 114
merged: 2026-10-11
branch: "fix/retrieval-spent-and-floor"
---

# Search: Count the chunk submitted with the auxiliary question only once, and do not set a floor for small rounds.

What. In v5 calibration execution b2, I reproduced the row where D missed the expected evidence using a local index (same corpus) to find and fix two causes. I did not send any requests for the reproduction.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #114 · `fix/retrieval-spent-and-floor`
