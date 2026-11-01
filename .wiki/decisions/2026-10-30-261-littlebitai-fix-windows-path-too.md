---
scope: project
severity: preference
triggers: []
domain: ''
title: "스펙이 큰 리팩터링 카드도 작업 셀이 Windows 명령줄 길이 한도에 막히지 않고 시작됩니다"
pr: 261
merged: 2026-10-30
branch: "LittleBitAI/fix-windows-path-too"
---

# 스펙이 큰 리팩터링 카드도 작업 셀이 Windows 명령줄 길이 한도에 막히지 않고 시작됩니다

What. `tool/agent/chat_session.py` - Claude 세션의 시스템 프롬프트를 파일로 쓰고 `--append-system-prompt-file`(격리 세션은 `--system-prompt-file`)로 경로만 넘깁니다. 명령줄 길이가 스펙 크기와 무관해집니다. - 파일은 `tempfile.mkstemp` 로 프로세스마다 하나, 소유자 전용(`0600`)으로 만듭니다. 공유 `/tmp` 에서 다른 사용자가 스펙을 읽지 못합니다. …

Why. ai-coding-wiki 의 리팩터링 카드 `preserve-repository-boundaries-3578a758` 에서 [시작] 을 누르자 `FileNotFoundError: [WinError 206] 파일 이름이나 확장명이 너무 깁니다` 로 바로 끝났습니다(`raw/errors.jsonl`, 2026-10-30T13:01:11Z). 작업 셀은 스펙 전체를 시스템 프롬프트로 만들어 `--append-system-prompt` 인자로 넘겼습니다. 이 카드는 1단계 점검 결과(`refactor` 41KB)와 goal·out·done 이 커서 프롬프트가 73,332자, 명령줄 전체가 75,158자였습니다. …

Source. PR #261 · `LittleBitAI/fix-windows-path-too`
