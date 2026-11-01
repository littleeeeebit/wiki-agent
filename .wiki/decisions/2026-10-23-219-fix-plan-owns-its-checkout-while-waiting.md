---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Prevent chat turns from using the worktree while the plan is awaiting an answer, and ensure posting is not stopped due to decision logs."
pr: 219
merged: 2026-10-23
branch: "fix/plan-owns-its-checkout-while-waiting"
---

# Prevent chat turns from using the worktree while the plan is awaiting an answer, and ensure posting is not stopped due to decision logs.

What. `work.begin` 이 작업트리의 계획이 시작부터 인계 사이(`handoff`·`stopped` 가 아닌 단계)에 있으면 턴을 409 로 거절합니다. 답을 기다리는(`clarify`) 동안 계획은 작업트리 hold 를 놓지만 작업트리는 여전히 계획의 것입니다. 메시지는 질문을 계획 카드에서 답하라고 안내합니다. - `planning.clean` 이 계획 폴더 밖의 untracked 파일을 커밋 안 된 변경으로 치지 않습니다. …

Why. ai-companion-voice-agent refactoring plan `plan-32916e65` stopped (P0) with "Stopped — worktree moved · HEAD moved at 13c11f0". 22:29 Plan started, planner issued one question and waited at `clarify`. The question is also visible in the chat for that checkout. 06:28 Human inputs "Yes, let's go through the steps." into the agent box → normal write turn (session 5cfae6b7) writes the plan itself, commits 5d0073f6, pushes, and PR #247. 06:56 Planner continues to write a 10-step draft and stops at 07:44 when posting sees that HEAD has moved from base. …

Source. PR #219 · `fix/plan-owns-its-checkout-while-waiting`
