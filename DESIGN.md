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
    question-title: "sans 16px / 600"
    question-detail: "sans 14px / 400"
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
  - 오른쪽 — 작업 머리글 · 코드 변경 현황 (접힘) · 에이전트 · 리뷰 · 터미널 탭
  - 답 — 진행 (검색 → 그래프 확장 → 답변 → 게시) · 멈춤 · 근거와 판단 보기 (펼침)
  - 지도 — 층 토글 · 찾기 · 되돌리기 · 지표 한 줄 · 옆 패널 · 이번 질문 경로 층과 경로 목록
  - 모달 — 설정 (일반 · 연결 · 질문 (Jev) · 리뷰) · 리뷰 루프 PR 고르기 · 연결 확인
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
| Right | `1fr` | Selected task. A collapsed code-change disclosure sits above [Agent · Review · Terminal], outside transcript scrolling. If waiting for approval, a `wait` dot appears on the agent tab |

Local windows use the 3-column layout at 1280px or wider. From 1101px through
1279px, the rail collapses to `3.25rem` of icons and task dots. At 1100px or
narrower, a local window shows one full-width pane and bottom navigation.

Paired phones use focused portrait views and a compact PC-style landscape workspace.
In APK 0.1.3, the native overflow menu explicitly selects Portrait or Landscape.
One saved choice owns both the requested Activity orientation and the web layout.
Neither sensors, the system auto-rotate setting nor viewport shape choose the
mode; both menu choices remain enabled. The former web three-pane preference
is ignored. A shared 48px header names the project or task and keeps Settings
and the native menu reachable. Conversation offers Map there.
Portrait uses 48px bottom navigation. Landscape shows a 180px task rail,
conversation (`1.1fr`) and selected task (`1fr`) simultaneously below the shared
header. List (`목록`) folds the rail; Together, Conversation and Task
(`함께`, `대화`, `작업`) choose both content panes or one focused pane without
discarding drafts. Each pane retains its own compact focus/tabs row, options
disclosure and composer. Only remote clients enter this landscape layout; local PC windows
retain their existing columns, breakpoints and controls, even when wide and
short. Pane headers and the rail's duplicate app title disappear; panes stay
mounted. The browser companion has no native screen-rotation control.
Task model controls sit behind Task options. Token usage stays beside the task
model label. Specification details, editing, start/reopen controls and planning
questions live in Next Task, with the Korean overlay over generated requirements.
CLI quota windows live at the bottom of the task rail. Project/review
tools are collapsed in the task list. Live diff counts remain visible, but
the diff itself starts collapsed on phones. Stop and session permission
revocation remain visible while applicable.

The center is slightly wider than the right to give the conversation more reading space. Desktop conversation and agent toolbars wrap their controls to the available pane width and grow from a `44px` minimum height. Focus tabs, document scope, model choices, planning and clear context remain reachable on 16:10 screens and scaled desktop windows. Clear context is an icon on both sides, with its name in `aria-label` and `title`.

The title headers of the three sides have the same height (`44px`, `h-11`). They only state what is being viewed. The tab line is `36px` (`h-9`). Settings have been moved to a modal via the gear icon — since translation and theme are things you don't touch again once set, there is no reason to use rail space.

## Color

Neutral background with two accent colors. Selected by the user on 2026-09-25.

- `ok` Blue is for selected items and clickable items — focus, citations, primary buttons, active sessions
- `wait` Amber is only for writing that is waiting for a person — approval cards, allow buttons, dots in the task tree waiting for approval. Since it is the thing to find first when returning after looking at another window, it is the only warm color on the screen. If used elsewhere, it loses its purpose

`warn` is used only for failure. A disconnected state is a state, not a failure, so it is `ink-faint`.

`add`·`del` are used only for the `+`·`-` lines of a patch. Since it is a direction, not a state, values like `ok`·`warn` are not used — deleted lines are not errors.

Dark is the default. Light is selected in the general settings modal and remembered per machine.

Landscape workspace controls fill only the selected mode with existing `primary`
and use `primary-foreground` for its label. The user chose Filled blue over
Blue outline on 2026-10-03. This changes emphasis, not the established palette. Browser-measured
label contrast is 7.26 in dark and 5.66 in light; the outlined candidate is
6.62 and 5.66. No desktop colour or token changes.

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

### A question's run

No new colours (Jev stage 9, 2026-09-27). Every state is also a mark and a word, so none rests on colour: the stepper is `✓` passed, `●` now, `○` to come; evidence support is `✓` supported (`ok`), `?` unverified, `≠` conflict, `!` untrusted, `·` not cited (all `ink-soft`). `warn` stays for failure only — a cancelled run is `⏹ 멈춤` in `ink-soft`, since stopping is a person's choice, not a fault. On the run's map layer a seed is the large dot, a bridge the dashed one, cited evidence the outlined one, said in a legend above the path list.

### Map

The map follows the app tokens (`web/src/graph/map.css`). Repository documents are neutral (`map-doc`), and only injected knowledge pages are colored `map-page`. Module pages are dashed, and decision records are small and light. Hub rules go from dark to light in layer ladder `map-ladder` 1–5, and rules that do not apply to this repository are faint and dashed.

## Typography

The compact chrome uses seven stages. Question cards add two reading stages
for longer decisions. When measured on 2026-09-24, there were 19 types. When
rebuilt and measured at loop stage 6, mono 11·11.5·12.5, sans 12·13, and 10px
were included, so those were moved to the seven chrome stages below.

| Stage | Value | Use |
| --- | --- | --- |
| title | heading 15 / 600 | App name |
| pane | heading 14 / 600 | What the pane is currently — focus, task name, document title in map panel |
| label | heading 11 / 600 | Section name — To-Do · In Progress · Done on the rail, specification, General · Connection · Review in settings, table headers |
| body | sans 13.5 / 400 | Reading — questions, answers, instructions, empty states. Only emphasis within answers is 700 |
| control | sans 12.5 / 400 | Clickable or selectable items, single-line descriptions, status sentences |
| code | mono 12 / 400 | Paths, task · repository names, tool lines, approval content, code within answers |
| meta | mono 10.5 / 400 | Numbers, time, cost, branches, node names and metrics on the map. The status phrase of a task and PR · round are also this stage |
| question-title | sans 16 / 600 | Questions and option titles |
| question-detail | sans 14 / 400 | Descriptions, chapter navigation and question actions |

sr-only labels (project, model, inference intensity) are measured at the browser default of 16px, but since they are not visible on screen, they are not counted in the stages.

## Spacing

Question cards use a reading scale separate from compact tool chrome:
16px questions and option titles, 14px descriptions, line height 1.5 or more,
16px card padding and 24px between chapters. Native radio and checkbox
controls retain keyboard navigation. Examples use Markdown and fenced text
sketches. The existing user-selected palette remains the reference.

The task pane shows actual Git changes with additions and deletions, refreshed
every second while running. Its disclosure starts collapsed and stays above the
tabs across transcript scrolling and tab changes. The expanded preview scrolls
within its own bounded area. The rail footer shows each CLI's reported quota
windows separately, including five-hour and seven-day limits, with an absolute
reset date and time. The task model row shows input and output tokens. Connection
setup duration and connection labels are omitted; unavailable quota values remain
explicitly unavailable. Compaction starts and finishes appear in the transcript;
an ongoing agent compaction also has a visible status above the reading area.

The expanded code-change disclosure lists all modified files, with a second
disclosure per file for its own diff. Paths use code type, controls use the
control stage, and additions/deletions retain `add`/`del`. The list remains
within 35dvh and an expanded patch within 25dvh. Task model options include
FAST OFF by default, with an explicit switch state and provider support hint.
The center pane's App structure tab shows the selected repository's local
`.omm` diagram, module selector and source inventory. Missing documents offer
an explicit Add .omm action; the repository name stays visible above it.
Diagram-loading errors offer a screen reload. Diagram surfaces remain neutral;
selection and enabled FAST use the existing blue interaction token. Diagram
text and source inventories use the code stage. Diagrams fit the available
width initially; natural-size mode scrolls within the container. No separate
dashboard is added.
The user chose the outlined blue FAST ON state on 2026-10-04. Browser-measured
label contrast is 6.27 in dark and 4.50 in light; the filled alternative is
7.26 and 5.66. The palette and semantic roles remain the same.

Only multiples of 2px are used. Chrome controls are `28px` (`h-7`). Question
actions are at least `44px`, and option cards grow with their descriptions.

The mobile companion reuses the established palette and type families. Reading
text and inputs are 16px on phones; touch controls have at least 44px hit areas.
The shell uses the dynamic viewport and safe-area insets. Pane navigation keeps
conversations mounted so switching views does not stop their streams. Settings
includes desktop-owned pairing controls and a locally rendered 232px QR code
with four modules of quiet margin. The QR stays black on white in both themes
for scanning, and encodes the same single-use pairing link. The phone sees
connection guidance for the single-view layout. The install card
uses the existing blue primary action selected by the user on 2026-10-03;
no new palette.

Desktop Mobile settings orders onboarding as 1. Install Android app, then
2. Connect to PC. Each has a separately labelled 232px black-on-white QR with
four modules of quiet margin. The installation QR opens a download page and
contains no pairing secret. The phone page uses 16px reading text, one 22px
heading and one 48px blue download action inside a 384px maximum-width panel.
The existing dark panel, ground, ink and muted ink values are reused.

The Android shell has one native bootstrap screen before the shared companion:
Scan connection QR (`연결 QR 스캔`) is primary, Clipboard link
(`클립보드 링크 연결`) is its fallback. After connection, a 44px native overflow
target replaces the former 56px toolbar. Its menu offers QR scanning, clipboard
connection, reload and both screen modes. A one-way public DOM layout hint
reserves that target in the shared header and applies the explicit screen mode;
it exposes no JavaScript-to-native bridge or pairing data. The app retains
the existing palette and release signing key.

On 2026-10-03 browser checks measured 595px of task reading space at 400×800
in portrait. The landscape follow-up replaced the rejected one-pane side-navigation
model with simultaneous task list, conversation and task panes. At 844×390,
their measured widths are 180px, 347.80px and 316.20px; all start below the
48px header and both composers end at the viewport bottom. At 740×320,
both content panes still exceed 260px. Secondary controls remain disclosed;
unused reading space stays empty rather than becoming another status dashboard.
The local PC geometry matched the before-change baseline at 1440×900,
1440×390, 1200×390 and 1100×800. These measurements do not cover actual screen
rotation, real keyboard behavior or native overflow taps on a physical phone.

The task-monitoring update on 2026-10-03 keeps the established palette and type
scale. At 1440×900 the rail footer ends at y=900, its padding is 12px, and the
fixed diff summary uses 12px by 20px padding with 12.5px control text. Measured
summary contrast is 13.73 in dark and 17.76 in light. Desktop geometry remains
the same at the four sizes above; the folded 52px rail opens quotas in a bounded
240px popover. On a 400×800 phone the requested fixed diff disclosure consumes
45px, leaving 550px for the task transcript. Both task and mobile browser checks
pass without horizontal overflow. No new palette choice is introduced.
