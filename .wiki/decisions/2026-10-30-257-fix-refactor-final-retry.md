---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "Even if the final check of a refactoring card fails due to the environment, a human can rerun it, and the screen does not freeze even if the repository is changed during a long turn."
pr: 257
merged: 2026-10-30
branch: "fix/refactor-final-retry"
---

# Even if the final check of a refactoring card fails due to the environment, a human can rerun it, and the screen does not freeze even if the repository is changed during a long turn.

What. `tool/main/refactor_run.py` `verified` — If a human turn that started after a final check of the same head failed (or was interrupted) reaches a completion report, it runs one more time. The previous attempt remains in the `history` of the receipt. It does not rerun in turns started by the server or agent itself. - `web/src/lib/work.ts`, `web/src/lib/api.ts` — Holds only one turn stream reattached for each path. …

Why. 1. Final check failure of a refactoring card is reused forever. This happened in the ai-companion-voice-agent `latency-record-preparation-97d92512` card. The final check failed in two timing-sensitive tests due to a slowed-down machine (544 seconds, while the same commit passed in 334 seconds at other times). After that, even if a completion report was submitted again for the same head, the server did not run the command and returned the stored failure receipt as is (`최종 명령을 돌리지 못했다 — 기존 로컬 최종 검사: failed … 자동 재실행하지 않습니다`). Therefore, there was no way to open the PR. The work loop (`loop.finalized`) is set to rerun the failure of the same head one more time if a human continues it `verify. …

Source. PR #257 · `fix/refactor-final-retry`
