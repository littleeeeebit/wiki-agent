---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "에이전트가 고치는 코드가 실시간 창에 +/− 색 diff 로 보이고, 최종 게이트 남은 시간이 잘리지 않으며, 화면의 산문·중복 버튼·쓸모없는 분기를 걷어냅니다"
pr: 259
merged: 2026-10-30
branch: "fix/ui-slop-and-live-diff"
---

# 에이전트가 고치는 코드가 실시간 창에 +/− 색 diff 로 보이고, 최종 게이트 남은 시간이 잘리지 않으며, 화면의 산문·중복 버튼·쓸모없는 분기를 걷어냅니다

What. `tool/agent/chat_events.py` - `_tool_meta` 는 Claude 의 Write 를 전부 `+` 줄로, Edit·MultiEdit 를 unified diff 로 바꿔 이벤트 meta 의 `diff` 에 싣습니다. - `_patch_meta` 는 Codex `fileChange` 의 `changes` 에서 같은 모양의 diff 를 만듭니다. 파일마다 `▸ path` 머리가 붙습니다. …

Why. 작업 레일에서 최종 게이트의 예상 남은 시간이 `truncate` 로 잘려 "약 7분 남…" 처럼 보였습니다. 에이전트가 코드를 고칠 때 실시간 창에는 `Edit path` 같은 검은 한 줄만 떴습니다. 무엇이 빠지고 들어왔는지는 "코드 변경 현황"에서만 볼 수 있었습니다. 화면 전수조사에서 같은 종류의 문제가 더 나왔습니다. - 잘림: 레일의 상태 줄, 작업 이름, 파일 제목, 모델 선택 상자. - 버튼 중복: 승인 카드가 [변경 보기] → [승인] 두 단계였고, 설정은 구역마다 [저장] 이 따로 있었고, 레일에 "위키 N [연결]" 줄과 [프로젝트 · 연결] 버튼이 따로 있었습니다. …

Source. PR #259 · `fix/ui-slop-and-live-diff`
