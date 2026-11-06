---
scope: project
severity: preference
triggers: []
domain: ''
title: "Preventing the review loop from stopping due to model capacity"
pr: 180
merged: 2026-10-19
branch: "fix/review-capacity-backoff"
---

# Preventing the review loop from stopping due to model capacity

What. The review loop for rfg_rag #70 stopped for `라운드 형식` reason. The review cell produced the error "Selected model is at capacity. Please try a different model.", and the loop stopped immediately after one retry. A human had to press [Continue]. The edit turn, plan, refactor, and chat focus also fail in the same way with the same error.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #180 · `fix/review-capacity-backoff`
