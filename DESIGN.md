---
colors:
  ground: "#f5f4f1"
  ink: "#16181d"
  panel: "#ffffff"
  chip: "#ecebe6"
  rule: "#e6e3dd"
  edge: "#d9d6d0"
  ink-soft: "#4a5058"
  ink-faint: "#878d95"
  ok: "#3f6b8f"
  wait: "#8f5410"
  warn: "#a83a2c"
  add: "#2f6b4f"
  del: "#a83a2c"
  st-draft: "#6b7078"
  st-work: "#1f6cb0"
  st-review: "#7b52a4"
  st-ready: "#1d7d3e"
  st-queued: "#006669"
  st-stop: "#ba2b2e"
  map-doc: "#878d95"
  map-page: "#3f6b8f"
  map-module: "#b9b3a4"
  map-decision: "#d9d6d0"
  map-ladder: ["#2b3a67", "#3f6b8f", "#6b8ea3", "#93a8ac", "#b9b3a4"]
  dark:
    ground: "#14161a"
    ink: "#eceae5"
    panel: "#1c1f25"
    chip: "#232830"
    rule: "#262b32"
    edge: "#2e333b"
    ink-soft: "#a8aeb6"
    ink-faint: "#6f757d"
    ok: "#7fa9c9"
    wait: "#e0a458"
    warn: "#d9705e"
    add: "#7fbf9a"
    del: "#d9705e"
    st-draft: "#8a9099"
    st-work: "#6db6ff"
    st-review: "#c49bf3"
    st-ready: "#6fc884"
    st-queued: "#65d2d2"
    st-stop: "#ff7c75"
    map-doc: "#6f757d"
    map-page: "#7fa9c9"
    map-module: "#8a8577"
    map-decision: "#3a4049"
    map-ladder: ["#a9b8e8", "#7fa9c9", "#6b8ea3", "#5d7275", "#6b665b"]
typography:
  sans: '"IBM Plex Sans KR", system-ui, sans-serif'
  heading: '"IBM Plex Sans Condensed", "IBM Plex Sans KR", system-ui, sans-serif'
  mono: '"IBM Plex Mono", ui-monospace, monospace'
  scale:
    title: "heading 15px / 600"
    pane: "heading 14px / 600"
    label: "heading 11px / 600"
    body: "sans 13.5px / 400"
    control: "sans 12.5px / 400"
    code: "mono 12px / 400"
    meta: "mono 10.5px / 400"
spacing:
  unit: "0.25rem"
  steps: [2, 4, 6, 8, 12, 16, 20, 24, 32]
  rail: "15rem"
  rail-folded: "3.25rem"
  header: "44px"
  tabs: "36px"
  control: "28px"
  three-columns-from: "1280px"
rounded:
  radius: "0.375rem"
  sm: "0.225rem"
  lg: "0.525rem"
components:
  - 레일 — 앱 이름 · 톱니바퀴 · 프로젝트 고르기 · 모든 프로젝트 · 리뷰 루프 (N) · 작업 행 (할 것 → 도는 것 → 정리됨 → 끝난 것 N)
  - 가운데 — 대화 (다음 작업 · 위키 · 회고) · 지도 · 프로젝트 목록
  - 오른쪽 — 작업 머리글 · 명세 요약 (접힘) · 에이전트 · 리뷰 · 터미널 탭
  - 지도 — 층 토글 · 찾기 · 되돌리기 · 지표 한 줄 · 옆 패널
  - 모달 — 설정 (일반 · 연결 · 리뷰) · 리뷰 루프 PR 고르기 · 연결 확인
  - 빈 상태 (면마다 하나)
---

# DESIGN — Screen values for this repository

These are the same values as the tokens in `web/src/index.css`. If the two diverge, this file is the reference for comparison.

## Screen List

```
누가:   이 위키로 여러 저장소를 굴리는 한 사람
하려고: 다음 작업을 명세로 정하고, 맡기고, 리뷰를 돌려 머지한다
성공:   레일만 보고 사람이 할 일을 알고, 한 번 눌러 그 자리로 간다
```

It is a 3-column layout centered on tasks (loop stage 6, 2026-09-26). There is no dashboard or detail page.

| Area | Position | Why there |
| --- | --- | --- |
| Rail | Left `15rem` | Task (specification) rows are lined up in the order of To-Do → In Progress → Done. You know what to do just by looking at the rail |
| Center | `1.1fr` | [Conversation · Map]. The focus of the conversation is the next task · wiki · retrospective. "All Projects" swaps into this position |
| Right | `1fr` | Selected task. [Agent · Review · Terminal] tabs below the specification summary (collapsed). If waiting for approval, a `wait` dot appears on the agent tab |

1280px or wider is the 3-column layout. Below that, the rail collapses to the width of a `3.25rem` icon — gear, all projects, review loop, and one dot per task. There is no horizontal scroll at any width.

The center is slightly wider than the right because at 1280px, the conversation header (three focuses, two models, clear context) must fit on one line. If they were the same width, it would be `520px` and the header would need `574px`. Clear context is therefore an icon on both sides, and the names are in `aria-label` and `title`.

The headers of the three sides have the same height (`44px`, `h-11`). They only state what is being viewed. The tab line is `36px` (`h-9`). Settings have been moved to a modal via the gear icon — since translation and theme are things you don't touch again once set, there is no reason to use rail space.

## Color

Neutral background with two accent colors. Selected by the user on 2026-09-25.

- `ok` Blue is for selected items and clickable items — focus, citations, primary buttons, active sessions
- `wait` Amber is only for writing that is waiting for a person — approval cards, allow buttons, dots in the task tree waiting for approval. Since it is the thing to find first when returning after looking at another window, it is the only warm color on the screen. If used elsewhere, it loses its purpose

`warn` is used only for failure. A disconnected state is a state, not a failure, so it is `ink-faint`.

`add`·`del` are used only for the `+`·`-` lines of a patch. Since it is a direction, not a state, values like `ok`·`warn` are not used — deleted lines are not errors.

Dark is the default. Light is selected in the general settings modal and remembered per machine.

All accent colors exceed WCAG AA against the backgrounds and cards of both themes. The measured contrast is 6.6–8.3 for dark and 5.1–6.1 for light.

### Specification Status

The six `st-*` are used only for task dots on the rail and the status phrase in the right header (`phase` of `web/src/lib/tasks.ts`). On 2026-09-26, the user chose "vivid" — it is the side where the six are distinguishable even at 8px dots.

| Token | Status | Light Contrast | Dark Contrast |
| --- | --- | --- | --- |
| `st-draft` | Done | 4.53 | 5.13 |
| `st-work` | In Progress | 4.98 | 7.69 |
| `st-review` | PR · Waiting for Review · Review Rn · Fixing Rn | 5.33 | 7.33 |
| `st-ready` | Mergeable | 4.71 | 8.07 |
| `st-queued` | Waiting for Merge | 6.15 | 10.66 |
| `st-stop` | Stopped | 5.50 | 6.60 |

Contrast is the lower of the background or card. The OKLab distance between the seven including `wait` has the closest pair at 0.109 for light (Done–Waiting for Merge) and 0.102 for dark (Mergeable–Waiting for Merge). The candidate "subtle" was rejected at 0.075 because In Progress, Review, and Done were confusing at small sizes, and the candidate using the app's existing accent colors (`ok`·`add`·`warn`) as is was rejected because In Progress and Waiting for Merge were too close at 0.057 in light mode.

The dot for a task waiting for approval is `wait` instead of the status color. What a person needs to do takes precedence over status.

### Map

The map follows the app tokens (`web/src/graph/map.css`). Repository documents are neutral (`map-doc`), and only injected knowledge pages are colored `map-page`. Module pages are dashed, and decision records are small and light. Hub rules go from dark to light in layer ladder `map-ladder` 1–5, and rules that do not apply to this repository are faint and dashed.

## Typography

There are seven stages, and their uses do not overlap. When measured on 2026-09-24, there were 19 types. When rebuilt and measured at loop stage 6, mono 11·11.5·12.5, sans 12·13, and 10px were included, so all were moved to the seven below.

| Stage | Value | Use |
| --- | --- | --- |
| title | heading 15 / 600 | App name |
| pane | heading 14 / 600 | What the pane is currently — focus, task name, document title in map panel |
| label | heading 11 / 600 | Section name — To-Do · In Progress · Done on the rail, specification, General · Connection · Review in settings, table headers |
| body | sans 13.5 / 400 | Reading — questions, answers, instructions, empty states. Only emphasis within answers is 700 |
| control | sans 12.5 / 400 | Clickable or selectable items, single-line descriptions, status sentences |
| code | mono 12 / 400 | Paths, task · repository names, tool lines, approval content, code within answers |
| meta | mono 10.5 / 400 | Numbers, time, cost, branches, node names and metrics on the map. The status phrase of a task and PR · round are also this stage |

sr-only labels (project, model, inference intensity) are measured at the browser default of 16px, but since they are not visible on screen, they are not counted in the stages.

## Spacing

Only multiples of 2px are used. The height of clickable items is one `28px` (`h-7`).