---
scope: project
severity: preference
triggers: []
domain: ''
title: "단일 CLI 사용 팀원의 초기 프로젝트 연결 지원"
pr: 87
merged: 2026-10-07
branch: "fix/single-cli-onboarding"
---

# 단일 CLI 사용 팀원의 초기 프로젝트 연결 지원

What. Claude Code 또는 Codex 하나만 설치한 팀원 PC에서도 초기 연결이 두 CLI의 설정과 검증을 요구했습니다. 이제 PATH에서 찾은 CLI만 사용자 훅 설치, 프로젝트 훅 정리, 세션 검증과 연결 상태 판정에 사용합니다. Codex 신뢰 확인과 Claude 스킬 링크 처리도 해당 CLI가 있을 때만 수행합니다.

Why. 팀에서 공유하는 adapter는 두 도구를 계속 선언하므로 팀원마다 사용하는 CLI가 달라도 같은 프로젝트를 연결할 수 있습니다. 기존 adapter가 하나만 선언한 경우에도 허브 미리보기·확인용 digest·실제 적용·검증에 같은 선택을 유지합니다. CLI가 하나도 없으면 설치 안내와 함께 연결을 거부합니다. 확인 화면에는 적용할 도구를 표시하고, 필수 `gate_cmd`와 선택 `live_cmd`·`server_stop`, 프로젝트 훅 보호 규칙 설명을 명확히 했습니다. - 최종 커밋에서 연결·설치·훅·adapter·wiki 상태 관련 테스트: 77 passed. 연결·설치·wiki 상태 관련 테스트는 정방향·역방향 모두 53 passed. …

Source. PR #87 · `fix/single-cli-onboarding`
