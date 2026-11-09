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

## The run on the screen

### Entry and rail

- `새 프로젝트` on the project rail opens the intake form (step 2).
- A run appears on the rail under its folder name from S0 on, before the
  repository exists, with its stage as a short Korean label and a mark when it
  waits for the owner. A run that waits is the only thing that asks for
  attention; autonomous stages show progress without a mark.

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
| Fold | Sections the step marks folded start collapsed; opening one is recorded |
| Re-show | Changed items marked, unchanged ones collapsed |
| Submit | Enabled when every unfolded item is approved or edited; sends approvals and edits in one request |
| Length | The sheet scrolls inside its panel; the first screen holds what the step put first |

Screenshots open in a lightbox at full size; videos play inline. The palette
choice on R2 is a single choice among swatches.

### Recording whether a sheet was read

From the overview's "Measuring it": per gate the view records time open, which
folds were opened, and which items were edited, on the spec. When a later
stage finds an assumption wrong that was approved without being edited, the
run notes it against that gate. The numbers are shown to the owner in the
finished run's summary; nothing acts on them automatically.

## The feature board

The S5 board from step 4: four columns, cards draggable between them, the
reason under each card, the first-screen line above. Keyboard operable: a
focused card moves between columns with the arrow keys, since dragging is not
the only way a board must work. Submit sends the whole map once.

Built with the app's existing React and Tailwind; no drag-and-drop library is
added unless native HTML drag events prove unworkable in the Tauri window.

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

The run's state becomes the spec's `PR #n`, and from here the spec follows
the ordinary cycle in `docs/plans/loop/3-spec.md`: the owner starts Review
Loop, the rounds fix findings between agents, and the owner merges.

## The finished run's summary

Once the pull request is open, the run view keeps one summary: the owner's
sentence, the three gates with their rounds and reading numbers, the
autonomous stages with their cost and time, the critique rounds, and the
link to the pull request. The kind's saved profile has already been updated
in step 2.

## Completion

- Web tests for the gate sheet view (per-item approve and edit, submit
  enabled only when every unfolded item is answered, re-show marking), the
  board (drag and keyboard moves produce the same map), and the stage strip
  for each state including `stopped`.
- New tests in `tool/test_oneshot.py`: S8 refusing to open with the gate
  red, no workflow file for a private repository, the pull request attached
  to the spec by branch.
- The repository's gate including the web build and browser checks,
  `tool/lint.py --check` and `tool/debt.py check`.
- One real run end to end on a throwaway private repository: from the sentence
  to an open pull request with Review Loop started, recorded in this pull
  request with the three sheets, the board and the summary.

## Decided with the owner

Nothing yet for this step.
