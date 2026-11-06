---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "If you approve the deletion of the test file, the card will proceed on its own and show the estimated remaining time on the gate."
pr: 253
merged: 2026-10-29
branch: "feat/approval-resume-and-gate-eta"
---

# If you approve the deletion of the test file, the card will proceed on its own and show the estimated remaining time on the gate.

What. Automatic progression after approval (`specs.resumed`, `work.begin(replay=...)`) - Once approval is saved, the server checks the worker's last response. If there is a completion report, it re-evaluates that report without a model turn (contract → final command → PR). - If there is no report in the last response (e.g., if a human spoke after that), it requests a completion report from the worker only once. - If another turn is writing to the work tree, it does nothing.

Why. Even after pressing `[테스트 파일 삭제 승인]`(#252), the card remained stuck. The worker had already submitted a completion report, and the server had rejected that report in the test contract, but the approval only cleared the rejection reason and did not re-evaluate the report. Therefore, a human had to manually tell the agent to "submit a completion report" again (ai-companion-voice-agent `emotion-state-cleanup`). While the gate was running, the work queue only showed the elapsed time, so it was impossible to know when it would finish. As #252 was merged without a final gate, `refactor_run.py` (817 lines, limit 800) and `Blocks.tsx` (357 lines, limit 315) exceeded the debt ratchet. main is currently failing at `debt.py check`.

Source. PR #253 · `feat/approval-resume-and-gate-eta`
