# Step 5 — The oneshot screen and the first pull request

The overall design is in the [overview](0-overview.md). Steps 2–4 build the
stages with only the screen they need to be driven; this step gives the run
its own place on the screen, the three gate sheets their review UI, the
feature board its drag interaction, and ends the run by handing the first
pull request to Review Loop.

Goal. A person can start a oneshot run, see at a glance which stage it is in
and whether it waits for them, review each gate on one screen with per-item
approval, and find the finished pull request where every other pull request
is, with Review Loop ready.

## Parts

| Part | Scope | Module |
| --- | --- | --- |
| 5a | The run's row, the stage strip, the S2 conversation, the gate sheet view, the feature board and the reading record | `web/src/components/OneshotSpec.tsx`, `web/src/lib/oneshot.ts`, `GET /api/oneshot-specs` in `tool/main/oneshot_spec.py` |
| 5b | S8: the README turn, the final gate, the pull request handed to Review Loop, and the finished run's summary | `tool/main/oneshot_pr.py`, `tool/prompts/oneshot-readme.md`, the `Finished` view in `web/src/components/OneshotSpec.tsx` |

## The run on the screen

### Entry and rail

- `새 프로젝트` on the project pane opens the intake form (step 2).
- Before S1 ends, an unfinished run is listed in the intake form under
  `끝나지 않은 실행`, with its state or its stop. From S2 on it is also a row
  above the project list, read from `GET /api/oneshot-specs`: the
  repository's name, its stage as a short Korean label, and a mark when it
  waits for the owner (`r1`, `board`, `r2`, `r3`, or `spec` when the agent's
  question is the last word). A run that waits is the only thing that asks
  for attention; autonomous stages show progress without a mark. Either
  opens the run in the oneshot window.

### Stage strip

Across the top of the run view, the stages in order. Conversation stages and
gates are drawn differently from autonomous ones, so the owner sees that
there are three reviews and where they are. The current stage shows its
progress line (the work session's tool line, the build's gate attempt, the
loop's round count); a stopped run shows its reason and `[재개]`.

## The gate sheet view

One component renders R1, R2 and R3 from the server's sheet (steps 3 and 4
define the content and order). It owns only the interaction:

| Interaction | Behaviour |
| --- | --- |
| Per item | Approve or edit, with a comment box on edit |
| Fold | Folding changes what is shown, never what is decided. Sections the step marks folded start collapsed; opening one is recorded |
| Approve a section | The items the sheet's shown text does not name belong to its folded sections and are listed together under a header with "모두 승인 (N)"; pressing it records an approval for each of them, named in the request |
| Re-show | Items needing a decision again (edited, changed as a consequence, or reset by a dependency) marked and unfolded; untouched approved items collapsed |
| Submit | Enabled when every item on the sheet, folded or not, has a decision; the count of undecided items sits beside the button |

The server applies the same rule: a submission that leaves any item of the
sheet's current version undecided is refused, whatever the screen showed.
| Length | The sheet scrolls inside its panel; the first screen holds what the step put first |

Screenshots open in a lightbox at full size. A Mermaid fence in a sheet is
drawn as its diagram. The palette choice on R2 is a single choice among
swatches, and R2 carries `기능 보드 다시 열기`. Videos play inline once step 4
records walkthroughs.

### Recording whether a sheet was read

From the overview's "Measuring it": each submission of a gate sheet sends
the seconds the sheet was open and the folds opened, and the server adds the
items edited, from the submission itself, as one entry of
`oneshot.reading.<gate>` with the sheet's round. The numbers are shown to the
owner in the finished run's summary; nothing acts on them automatically. A
later stage that finds wrong an assumption approved without an edit is not
noted against its gate yet.

## The feature board

The S5 board from step 4: four columns, cards draggable between them, the
reason under each card, the first-screen line above. Keyboard operable: a
focused card moves between columns with the arrow keys, since dragging is not
the only way a board must work. Submit sends the whole map once.

Built with the app's existing React and Tailwind and native HTML drag
events; no drag-and-drop library is added. A card's focus follows it into its
new column.

## S8 — the first pull request

After R3 approval, a server routine and one model turn:

1. The model turn writes the README for the chosen OS (setup, run, test
   commands; both OSes when another is planned) and, for a public
   repository, the CI workflow. A private repository gets no workflow file;
   step 2's validity rule already forbids one.
2. The final gate runs in the worktree: the adapter's `gate_cmd`, which by
   now includes the backend, the frontend and the end-to-end tests.
3. The pull request is created as every spec's is: pushed with the person's
   `gh`, the body written in Korean by the existing `pr-write` turn from the
   diff, with the run's decisions as its `변경 이유` so `harvest` later
   records them.
4. The pull request is attached to the run's spec, so the Review Loop button
   finds it by its branch, as it does every task's.

R3's approval sets the stage to `pr` and sends the README turn
(`oneshot-readme.md`). When it ends, `oneshot_pr` checks, in order: the
`done-report` covers every `done` item; `README.md` exists; a workflow file
exists exactly when the repository is public with GitHub Actions; the
contract and the pytest configuration are pinned as in S4; and the adapter's
`gate_cmd` passes on a clean, committed HEAD (`specs.judge`). A refusal sends
the turn back, the third stops the stage. When all hold, the gate is recorded
on the spec with `selection: "full"`, and the next turn is the existing
`pr_text.writing` request with the run's decisions appended: each item the
owner edited at R1, R2 or R3, and the chosen palette. That turn's answer goes
through `specs.written`, which pushes the branch and opens the pull request
as for every spec; a push or `gh` refusal stops the run with its reason.
Resuming a stopped S8 starts again from the README turn.

The run's state becomes the spec's `PR #n`, and from here the spec follows
the ordinary cycle in `docs/plans/loop/3-spec.md`: the owner starts Review
Loop, the rounds fix findings between agents, and the owner merges.

## The finished run's summary

Once the pull request is open, the run's stage is `done` and the run view
keeps one summary: the owner's sentence, the three gates with their rounds,
the items edited and each submission's reading record, the run's spend as
totals (calls, cost and time), the critique and screenshot rounds, and the
link to the pull request. The kind's saved profile has already been updated
in step 2.

## Completion

- Web tests for the gate sheet view (per-item approve and edit; submit
  disabled while an item in an automatically folded section, or in a section
  the owner folded, is undecided; "approve all" deciding exactly its section;
  re-show unfolding every item that needs a decision again), the
  board (drag and keyboard moves produce the same map), and the stage strip
  for each state including `stopped`.
- New tests in `tool/test_oneshot_pr.py`: S8 refusing to open without its
  README, with a workflow file in a private repository, with a changed
  contract or with the gate red; a public repository needing its workflow;
  the pull request attached to the spec by branch and the summary; a refused
  push stopping the run; a resumed S8 starting from its README turn.
- The repository's gate including the web build and browser checks,
  `tool/lint.py --check` and `tool/debt.py check`.
- One real run end to end on a throwaway private repository: from the sentence
  to an open pull request with Review Loop started, recorded in this pull
  request with the three sheets, the board and the summary.

## Decided with the owner

Nothing yet for this step.
