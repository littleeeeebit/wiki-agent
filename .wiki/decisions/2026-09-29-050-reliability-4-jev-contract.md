---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev transition owner and call logs, graph health check (Reliability Level 4)"
pr: 50
merged: 2026-09-29
branch: "reliability-4-jev-contract"
---

# feat: Jev transition owner and call logs, graph health check (Reliability Level 4)

What. Every decision made by the server must be explainable in terms of who decided it, what was chosen, and how much it cost. In reality, within already approved search behaviors, it was asking Jev "Should I search?" again, call costs were scattered, ASK/ANALYSIS prompts were outside of versions, and no one had ever seen the actual graph state. Reliability Plan Level 4 (`docs/plans/reliability/4-jev-contract.md`).

Why. Decision Owner - `/api/jev`'s `decisions.coverage()` provides the owner (Jev, code, generation role, host), allowed operations, permissions held by the code, policy version, and fitted/provisional status for each decision point - Tool selection within the host session is specified as "not covered" Search decision deduplication - Searches approved by `work.start`·`loop.fix` are passed to `prepare(cause=...)` by `decisions.gathered` - If a search is already requested, only the "Should I search?" question is omitted from the routing request. The source selection, analysis necessity, and question parts are still determined by Jev. …

Source. PR #50 · `reliability-4-jev-contract`
