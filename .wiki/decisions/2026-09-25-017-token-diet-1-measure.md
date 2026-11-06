---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "feat: token savings ① — measurement tools (replay·latency·usage·ab) and trajectory's sent·full"
pr: 17
merged: 2026-09-25
branch: "token-diet-1-measure"
---

# feat: Token savings ① — Measurement tools (replay·latency·usage·ab) and trajectory's sent·full

What. PR ① among the 4-bundle plan for token savings in public copies (`docs/plans/token-diet-4-clone.md`) — measurement only. Instead of copy-pasting the code from the copy, it was rebuilt according to the original package structure (`wiki`·`workspace`'s `__all__`). Only the recording field is turned on. Line `trajectory` has `sent` (injection total UTF-8 bytes)·`full` (`[이름, 태그]` of the rule page sent without truncation). …

Why. Moved the recording after the output — `sent` is known only when the body exists, and lines written before the output leave `full` of dead turns in stdout. Early return was changed to filter only the output, so empty turns are also continuously recorded `compose` to `wiki` — `replay` must measure the same bytes as the hook. Using two sets causes misalignment `tx`·`txp`·`reset` were not included — they are the output of compact detection (`recall`), so they go with ②. And they cannot be used as markers to separate the two arms in the A/B test of ② (Discarded: all five fields in ①) `ab`'s path verification is seen not by the field shape of the line but by the injection's source line (`Hub wiki \`<worktree>\``) …

Source. PR #17 · `token-diet-1-measure`
