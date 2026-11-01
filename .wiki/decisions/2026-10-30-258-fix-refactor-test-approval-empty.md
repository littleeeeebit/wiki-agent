---
scope: project
severity: preference
triggers: []
domain: ''
title: "리팩터링 카드가 고친 TypeScript 테스트 파일도 사람이 읽고 승인할 수 있어, \"승인할 테스트 변경이 없다\"에서 멈추지 않습니다"
pr: 258
merged: 2026-10-30
branch: "fix/refactor-test-approval-empty"
---

# 리팩터링 카드가 고친 TypeScript 테스트 파일도 사람이 읽고 승인할 수 있어, "승인할 테스트 변경이 없다"에서 멈추지 않습니다

What. `tool/main/refactor_run.py` - `amendments` — Python 이 아닌 테스트 파일도 Git 패치와 함께 내놓습니다. 정의 이름 대신 HEAD 의 blob 을 싣습니다. - `approve_amendment` — 그 파일을 blob 에 고정한 채 `tests.contract` 에 선언합니다. …

Why. ai-companion-voice-agent `authentication-callback-cleanup-97d92512` 카드에서 일어난 일입니다. 카드가 `frontend/src/core/runtime/voiceRuntime.test.ts` 를 고쳤고, 서버가 `테스트 계약 밖의 변경 — … 의 기존 테스트 변경` 으로 거부했습니다(`raw/main.log` 의 task-failure). 사람이 `[테스트 계약 변경 승인]` 을 누르자 "승인할 테스트 변경이 없다." 가 떴고, 카드는 그대로 멈췄습니다. 조사 명세는 이 TS 파일의 테스트를 `파일::테스트 이름` 으로 `tests.contract` 에 올려 두었습니다. …

Source. PR #258 · `fix/refactor-test-approval-empty`
