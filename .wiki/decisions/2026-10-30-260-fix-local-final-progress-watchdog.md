---
scope: project
severity: preference
triggers: []
domain: ''
title: "로컬 최종 검사가 오래 걸리는 테스트를 멈춘 것으로 끊지 않고, 출력이 10분째 늘지 않을 때만 멈춤으로 판정합니다"
pr: 260
merged: 2026-10-30
branch: "fix/local-final-progress-watchdog"
---

# 로컬 최종 검사가 오래 걸리는 테스트를 멈춘 것으로 끊지 않고, 출력이 10분째 늘지 않을 때만 멈춤으로 판정합니다

What. `tool/main/runtime.py` - 모든 게이트 실행이 지나는 `_captured` 가 출력 크기를 1초마다 봅니다. 10분(`IDLE_SECONDS`) 동안 출력이 늘지 않으면 `검증 실행이 10분째 출력 없이 멈췄다` 로 끊습니다. 실행한 시간이 아니라 진행이 멈췄는지로 판단합니다. - 게이트를 `PYTHONUNBUFFERED=1` 로 돌립니다. 테스트가 끝날 때마다 찍는 점이 바로 기록돼, 진행 감시와 화면의 진행률이 실제와 맞습니다. …

Why. ai-generation 작업이 `멈춤 — 로컬 검증 준비 · 로컬 최종 검사 실패 — 검증 실행 시간 제한을 넘었다` 로 반복해서 멈췄습니다. ai-generation 의 최종 검사 기록(`.git/wiki-verification-*.json`)을 보면, 전체 테스트(925개)는 평소 15.3~18.8분 걸려 통과했습니다. 마지막 실행은 19.0분에서 끊겼습니다. 로컬 최종 검사는 고정된 19분(`verify.final(seconds=19 * 60)`)이 지나면 무조건 끊었습니다. 전체 시간만 보는 상한으로는 오래 걸리는 실행과 멈춘 실행을 가릴 수 없어서, 정상인 느린 실행이 멈춘 것으로 처리됐습니다. 출력이 61% 에서 멈춘 것처럼 보인 것도 오해를 키웠습니다. …

Source. PR #260 · `fix/local-final-progress-watchdog`
