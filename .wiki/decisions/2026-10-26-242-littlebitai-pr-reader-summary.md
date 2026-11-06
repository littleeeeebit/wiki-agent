---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Have the task PR text written in a dedicated turn right before the PR, referring to guidelines and examples, and remove P2 comment translation."
pr: 242
merged: 2026-10-26
branch: "LittleBitAI/pr-reader-summary"
---

# Have the task PR text written in a dedicated turn right before the PR, referring to guidelines and examples, and remove P2 comment translation.

What. Right before uploading the PR, after the gate passes, the server sends a separate 'write PR text' turn to the task agent. This turn contains guidelines and 3 pairs of examples (specification → PR title/body). - Location: `specs.pr_turn`, `specs.written`, `tool/prompts/pr-write. …

Why. The wiki-agent uploads the PR with the summary written by the task agent once the task is finished (#241). However, the summary uploaded to ai-companion-voice-agent #262 was just a list of function names even though it passed all format checks, so someone unfamiliar with this pipeline could not tell what had changed. The instruction to write a PR summary was only included as a few lines within the long instructions given at the start of the task, so it was far removed by the time the summary was actually written. A PR is a record to be read by people who will work together later. A PR that describes only the code without context becomes an obstacle to collaboration, and the meaning understood by the agent becomes blurred when passed through a lightweight translation model.

Source. PR #242 · `LittleBitAI/pr-reader-summary`
