# Step 6 — Screen and Map

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. Remove the temporary patches from steps 3–5, and build the task-centric 3-column layout from scratch in the order of `design-pass`.
The map follows the rail's projects and draws the document graph of that repository.

## User Agreements

2026-09-25.

| What | Agreement |
| --- | --- |
| Project List | Swap the center panel. "All Projects" under Rail's project selection. Only Hub and Research settings in the `연결` section of the settings modal |
| Rail Order | Human-led (Pending Approval, Mergeable, Stopped) → Rotating (In Progress, Review, `머지 대기`) → Done. Merged items are folded into "Done N" at the bottom and disappear when the task tree is cleared. `머지 대기` is placed in Rotating because there is no human action required, and "Queue" or "Auto-Merge — Waiting for Checks" is written on the second line. Once it leaves the queue, it becomes `멈춤` and moves up |
| Color | Re-select the palette. Candidates are proposed in step 4 of `design-pass` and the user selects one |
| Minimum Width | 3 columns for 1280px and above. Below that, the rail folds to icon width. No horizontal scrolling |
| Map Side Panel | Body preview (translation overlay), incoming and outgoing links, scope/severity/trigger, [Ask about this document] |

## Screen List

Step 0 of `design-pass`. One line is the one-line purpose of the overview.

```
누가:   이 위키로 여러 저장소를 굴리는 한 사람
하려고: 다음 작업을 명세로 정하고, 맡기고, 리뷰를 돌려 머지한다
성공:   레일만 보고 사람이 할 일을 알고, 한 번 눌러 그 자리로 간다
```

| Area | What |
| --- | --- |
| Rail | Project selection and "All Projects", gear icon, "Review Loop (N)", task (specification) row |
| Center | [Chat · Map] tab. Chat focus is on next task/wiki/retrospective. Project list swaps into this spot |
| Right | [Agent · Review · Terminal] tabs under the selected task's specification summary (folded). If waiting for approval, a `wait` dot appears on the Agent tab |
| Settings Modal | General (translation, theme), Connection (hub status, research switch/limit/model), Review (round limit, concurrent execution, review model) |
| Modal | PR selection for review loop, hub migration confirmation, research estimate confirmation |

One task row in the rail.

```
● fix-login-redirect        #12 R2
  리뷰 중 · 승인 1
```

The first line is the specification `id`, PR, and round. The second line is a status snippet and human action. A task tree without a specification also becomes a row,
and the second line is "No Specification".

## Walkthrough

| What | Instead of |
| --- | --- |
| All temporary screens from steps 3–5 — temporary card placement, temporary setting input fields, temporary lists | Above areas |
| "New Task" input field and `POST /api/worktrees` | [Start] in the specification. If an empty task tree is needed, a one-line specification in the next task focus |
| "→ Task" draft, `/api/draft`, `WRITERS` | Specification. The two buttons in the retrospective candidate pass that candidate as material to the next task focus |
| Rail position for translation/theme switches | General in settings modal |
| Top/bottom split of the right side | Tabs |
| Map title text and number cards | One line of metrics under the header |

When `/api/draft` is deleted, "To Wiki" and "To CLAUDE.md" in the retrospective become specifications. The instructions in `WRITERS` are moved to the next task focus's
specification `goal` and `out` — "One file only", "Do not touch other files" is `out`.

The "terminal changes appearing late in the task tree list" issue passed from step 1 is also closed here. The rail re-reads the list when leaving the window focus, loop
events, or terminal tab.

## Map per Repository

`GET /api/graph?repo=<이름>`. Currently, it provides the hub's `graph.json` as a file (`tool/main/app.py:192`).

| Layer | What | From where |
| --- | --- | --- |
| This repo document | Documents and links, decision records | `repo_graph.build(repo)` from memory. `write` is not called (writes to original) |
| Hub rules | Hub pages and their triggers applied to this repository | `graph.load_pages` where `scope` is caught by that repository, `graph.build` shape |

- Layers are toggles in the header — This repo document, Hub rules, both. Default is This repo document
- Selecting Hub shows the current policy graph as is
- One line of metrics — page count, orphan documents (no incoming links), `repo_lint` warning count
- `.wiki/modules/*.md` (step 5) is a node in the This repo document layer. It is marked as not being injected
- The map follows the app theme. Current color constants are converted to tokens
- Side panel. Body is the beginning of `/api/file` as a translation overlay. [Ask about this document] puts
  `` `<path>` `` into the wiki focus input field and switches the center to chat

- If calculation is heavy, the latest modification time of the file per repository is kept in memory as a key.

## Sequence

`design-pass` five steps. Values are written in [`DESIGN.md`](../../../DESIGN.md)].

| # | Task |
| --- | --- |
| 1 | Purpose and layout. Set up areas from the screen list above and rewrite `DESIGN.md` for this window |
| 2 | Measure padding, width, and alignment at 1280/1440/1920px and 1279px (folded rail). Click everything — candidates, options, card save, [Start], three approval buttons, [Stop], [Continue], review loop modal, [Merge], [Accept]·[Re-PR], [Connect], [Retry], layer toggle, nodes, settings |
| 3 | Font hierarchy. Status snippet and PR/round display use one step |
| 4 | Propose palette candidate sets and have the user choose. The six specification states and `wait` must be distinguishable. Measure contrast |
| 5 | Remaining empty spaces |

## Test

No screen test framework. Server-side only.

- `/api/graph?repo=` writes nothing to the original — modification times of `git status --porcelain` and `.wiki/graph.json`
  before and after calling are the same
- Hub layer does not load `project` pages not caught by that repository
- `/api/draft`·`POST /api/worktrees` do not exist. `lint`'s public entry point check catches calls to deleted names

## Non-goals

| What | Why |
| --- | --- |
| 3 columns below 1280px | User decision. Fold the rail |
| Map editing | Map is read-only. Editing is done via specification |
| Mobile | This app is for desktop use |

## Verification

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- Leave the measurement table from step 2 of `design-pass` in the "Done" section of this document
- Start a specification in another window, run a loop, wait for approval, and check if the rail in this window shows the order and dots correctly

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Walkthrough | Temporary screens, drafts, "New Task" | Done |
| 2 | Map Server | `/api/graph?repo=`, layers, metrics | Done |
| 3 | design-pass 1–3 | Layout, measurement, font | Done |
| 4 | design-pass 4–5 | Palette selection, empty spaces | Done — "Vivid" |
| 5 | Gate | All verifications above | In progress — Auto-check passed, window verification remaining |

## Done

2026-09-26.

### Server

- Deleted `POST /api/worktrees`, `/api/draft`, `WRITERS`. Tests verify that the two paths do not exist
- `repo_graph.picture(repo)` — Documents, knowledge pages, modules, decision records and links, orphan documents. If `corpus.json` is missing,
  collect from memory. Writes nothing
- `GET /api/graph?repo=` — `layers.repo`, `layers.hub` (if hub, `graph.json` as is, otherwise to that repository
  `graph.build`), `metrics` (pages, orphans, `repo_lint` warnings). Selecting Hub itself defaults to Hub rules layer
- `loop.landed()` leaves "Queue" / "Auto-Merge — Waiting for Checks" as `queued` in `머지 대기`. Rail second line reads it
- `GET /api/connect` provides hub status (`hub`) together — Connection section of settings modal
- No cache. Heaviest repository was 2.7 seconds

### Screen

Deleted `Rail`·`WikiMap`·`graph/force.ts` of `web/src/` and rebuilt with `TaskRail`·`RepoMap`·`graph/engine.ts`·`Modal`·
`Settings`·`SpecSummary`·`lib/tasks.ts`. Values are [`DESIGN.md`](../../../DESIGN.md)].

### design-pass step 2 measurement

Measured width using the same source iframe within the same window (`scrollWidth - 폭`, overflowing elements, grid columns).

| Width | Grid Columns | Horizontal Overflow | Overflowing Elements |
| --- | --- | --- | --- |
| 1279 | 52 / 613.5 / 613.5 → After fix 52 / 642.7 / 584.3 | 0 | None |
| 1280 | 240 / 520 / 520 | 0 | Chat header focus `nav` 207 > 152 |
| 1280 (After fix) | 240 / 544.75 / 495.25 | 0 | None |
| 1440 | 240 / 628.6 / 571.4 | 0 | None |
| 1920 | 240 / 880 / 800 | 0 | None |

At 1280 and 1279, the map (including node selection panel) and the right side selecting tasks also had 0 overflow.

Found and fixed items.

| What | Fixed |
| --- | --- |
| Rail doesn't fold at 1279px | Tailwind v4 `max-[1279px]` is `width < 1279px`. Changed to `max-[1280px]` |
| Chat header doesn't fit on one line at 1280px (574 needed, 520) | Center `1.1fr`, context clearing to icon |
| Header `gap` split into 12 and 8 | All three to 12 |
| Map edge node names cut off | Wall distance to half of name width |
| Decision records without links stuck to wall and overlapping | Repulsion only within 240px. Removed date/number from decision record name |
| [Undo] covers bottom node name | To map header |
| "Review Loop (1)" opens loop immediately without modal | Goes through selection modal even if only one PR |
| Six steps outside of font size 10–13.5 | To seven steps of DESIGN |

What was clicked — chat and map, the three layers, a node, [ask about this document] (puts `` `.wiki/plan-active.md` `` in the wiki focus input),
Search, settings modal (reading translation/theme/research settings), all projects, ← back, rail row, expand specification summary, three right tabs,
[Connect]. Also saw the map change following tokens in light theme.

[Connect] connects immediately if there is nothing to verify (hub migration, research) (step 5 decision). Thinking the confirmation modal would appear,
I clicked `book`, `.wiki/`·`.claude/settings.json` were created in that repository, and SessionStart test ran.

Items not clicked — Candidates, options, card save, [Start], three approval buttons, [Stop], [Continue], review loop modal, [Merge],
[Accept]·[Re-PR]. Currently, there is only one specification `정리됨` and no open PRs. Will click during window verification in gate 5.

### Palette

Candidate sets. Candidates using the app's existing accent colors were removed because In Progress/Merge Pending were too close at OKLab 0.057 in light mode.
Of the remaining "Subtle" (closest pair 0.075) and "Vivid" (0.102), the user chose "Vivid". Values and contrast are `DESIGN.md`'s
Color → Specification State.

### Empty Spaces

The right side is emptiest when no task is selected. Left blank as there are no numbers to place or explanations to give.
