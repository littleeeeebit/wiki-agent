---
scope: project
severity: preference
triggers: []
domain: ''
title: "Answer path B: Fact answer verification, synthesis instead of rejection"
pr: 112
merged: 2026-10-11
branch: "feat/answer-path-b"
---

# Answer path B: Fact answer verification, synthesis instead of rejection

What. Ask Jev together in routing whether it is a progress question. - The question name is `progress`, and the number of calls does not increase when carried in the same request. - Registered in Jev kind, digest, purpose table for tracking, and list of decision points. - Changed the `verify_claims` default value of `grounded` to `None`. `None` is a general answer. - Verify only when Jev is certain that it is "not a progress question" and it is not an analysis question either. ...

Why. #111 Implement option B of the design. After #59, general answers are synthesized without verification, making the ratio of baseless claims in answers that went through Jev (0.212) the same as the side without Jev (0.218). B verifies only fact questions by claim unit. If the verification result cannot answer the question, the entire answer is not rejected, and the synthesized answer is posted as `unverified`. The four constraints of #59 are maintained as is.

Source. PR #112 · `feat/answer-path-b`
