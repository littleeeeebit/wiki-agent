---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Reliability PR 5 v3: Judgments handled by the code are not asked to the host, and mechanical tasks are not analysis (routing passed, latency 19.3→10.76 seconds)"
pr: 53
merged: 2026-09-29
branch: "fix/reliability-5-latency-routing"
---

# Reliability PR 5 v3: Judgments handled by the code are not asked to the host, and mechanical tasks are not analysis (routing passed, latency 19.3→10.76 seconds)

What. Two items remained unresolved from the v2 baseline of PR #52.

Why. Additional latency gate failure: p95 19.3 seconds, target 10 seconds - Analysis/fact routing: Undeterminable. This PR fixed the causes of both, and re-measured with a new hold set (v3). Routing passed. Latency decreased from 19.3 seconds to 10.76 seconds, but it is still a failure as it slightly exceeds 10 seconds. Per the repository owner's decision, v3 is released as the baseline, and the remaining latency will be addressed in the next PR (v4). - Latency - D's response generation time was similar to A's. The increased time was D's search phase. In a typical row, it took 2 host turns (about 5 seconds each). - Both turns came from types of judgments that the code already handles safely even if Jev answers "uncertain". - source: If uncertain, it searches the source. …

Source. PR #53 · `fix/reliability-5-latency-routing`
