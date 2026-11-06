---
scope: project
severity: preference
triggers: []
domain: ''
title: "Reliability PR 5: English baseline confirmation (v1·v2) and host model fallback for Jev uncertainty judgment"
pr: 52
merged: 2026-09-29
branch: "feat/reliability-5-english-baseline"
---

# Reliability PR 5: English baseline confirmation (v1·v2) and host model fallback for Jev uncertainty judgment

What. We are measuring and publishing the reliability baseline for English input and English evidence standards in two fixed sets (v1, v2). The gate remains v3 as is, and the threshold has not been changed.

Why. v1 (`eval/jev/reliability/`): 144 intents, 36 action fixtures. Labels were reviewed by gpt-6-sol. Answer support failed due to coverage (D−A −0.174). This is because D withheld the answer where Jev judged it as uncertain. - Fallback: Questions left uncertain by Jev are asked to the running host model once per request (`contract.decide`, all judgment types). The answer determined by the host is published and stored as `host_checked`, and is never counted as verified. Record the fallback rate by judgment type. Received merge approval after 4 rounds of code review by sol. …

Source. PR #52 · `feat/reliability-5-english-baseline`
