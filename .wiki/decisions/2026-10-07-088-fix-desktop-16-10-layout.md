---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "16:10 PC 화면의 도구 모음 겹침 수정"
pr: 88
merged: 2026-10-07
branch: "fix/desktop-16-10-layout"
---

# 16:10 PC 화면의 도구 모음 겹침 수정

What. 16:10 PC 화면에서 대화 초점 탭과 문서 범위·모델 선택이 겹치는 문제를 수정합니다. 대화·에이전트 도구 모음이 패널 너비에 맞춰 줄바꿈하고 높이를 늘려, 제어 버튼과 입력창이 화면 안에 유지됩니다.

Why. 변경은 데스크톱용 CSS에 적용했으며, 디자인 기준 문서와 브라우저 회귀 검사를 함께 추가했습니다. 검증: - 16:10: 1280×800, 1440×900, 1680×1050, 1920×1200, 2560×1600 - 16:9: 1280×720, 1366×768, 1920×1080 및 작은 PC 창 두 크기 - `python web/tests/desktop_browser.py`, `python web/tests/mobile_browser. …

Source. PR #88 · `fix/desktop-16-10-layout`
