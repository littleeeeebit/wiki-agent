---
scope: project
severity: preference
triggers: []
domain: ''
title: "Resolve 5 existing ratchet debts and standardize verification code"
pr: 97
merged: 2026-10-08
branch: "fix/repository-debt-baseline"
---

# Resolve 5 existing ratchet debts and standardize verification code

What. Resolve 5 failures in existing repository debt checks without changing existing criteria.

Why. Separated session event types and display functions, and organized task tree deletion to reuse existing `forget()`. Enlarged test files were separated by responsibility, while test names, bodies, verification values, and existing CLI test scripts were maintained. Four browser verifications use the same server start function and clean up the corresponding verification socket upon start failure. | Item | Before Change | After Change | Existing Limit | | --- | --- | --- | --- | | `tool/agent/chat_session.py` lines | 1,360 | 1,288 | 1,291 | | `tool/main/work.py` lines | 1,066 | 1,060 | 1,060 | | `tool/test_agent. …

Source. PR #97 · `fix/repository-debt-baseline`
