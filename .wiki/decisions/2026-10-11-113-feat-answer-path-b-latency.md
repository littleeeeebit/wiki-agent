---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Response path B delay: Removing host turn from general response verification"
pr: 113
merged: 2026-10-11
branch: "feat/answer-path-b-latency"
---

# Response path B delay: Removing host turn from general response verification

What. In claim verification for general responses (`verify_claims` None), host judgment is not used (`Grounding.fallback`). - Claims that Jev is not confident about are excluded from posting. - If the question cannot be answered, the synthetic alternative path in #112 takes over. - Calls that pass an explicit `verify_claims` will continue to ask the host as they do now. …

Why. Response path B, introduced in #112, had an additional delay (p95 difference between D and A) of 16.73 seconds, exceeding the 10-second gate. Diagnosing by the number of calls in the calibration row, most of the delay was due to the host judgment turn in the claim verification stage. This is a turn where Jev asks the host again for judgments it is not confident about, which occurred 120 times in row D 66 and takes about 4.5 seconds each. According to the owner's decision, this turn will not be used in general responses.

Source. PR #113 · `feat/answer-path-b-latency`
