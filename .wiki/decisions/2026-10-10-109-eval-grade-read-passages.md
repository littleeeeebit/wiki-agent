---
scope: project
severity: preference
triggers: []
domain: ''
title: "Make the grader also see the evidence read by the answer host (v5 calibration diagnosis)"
pr: 109
merged: 2026-10-10
branch: "eval/grade-read-passages"
---

# Make the grader also see the evidence read by the answer host (v5 calibration diagnosis)

What. `tool/agent/chat_session.py`: If the caller passes a list to `ChatSession(results=[...])`, the session includes the tool results received by the host in that list without truncation. - Claude: The entire `tool_result` block. Since the tool event body is truncated at 4000 characters for display, this is stored separately. - Codex `exec`: Completed commands (`aggregated_output`) and MCP call results. Previously, there was no result event itself. …

Why. The answer gate result of v5 calibration (`bc838d8`) dropped significantly compared to v4. Based on the same 72 cases, v4 was 0.736, and this time it is 0.141 (target 0.25). | | A groundless ratio | D groundless ratio | D number of claims | | --- | --- | --- | --- | | v4 | 0.423 | 0.112 | 179 | | v5 | 0.307 | 0.264 | 501 | The diagnosis results show two causes. 1. Product change (#59). v4 was measured at `a0e88d3`, that is, before #59. - After #59, general answers record synthetic results as `unverified`, and the claim verifier runs only when it is `verify_claims=True`. …

Source. PR #109 · `eval/grade-read-passages`
