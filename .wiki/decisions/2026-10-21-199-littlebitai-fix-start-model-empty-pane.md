---
scope: project
severity: preference
triggers: []
domain: ''
title: "Modify it so that any model selected in an empty agent slot is used at the start of the specification."
pr: 199
merged: 2026-10-21
branch: "LittleBitAI/fix-start-model-empty-pane"
---

# Modify it so that any model selected in an empty agent slot is used at the start of the specification.

What. Slots without a task tree use an empty slot key that is unique per repository. This includes slots where nothing is selected, slots pointing to a disappeared task, and slots for a specification before it starts. - `시작` sends the selection of the empty slot regardless of which card is pressed. - A started task inherits that model as its own selection. - A task that has already started (opening a branch) maintains its own model (`cell`) as it does now.

Why. Even after deploying #196, pressing `시작 ▸` on the specification card caused the first turn to revert to the Claude default (P0). Actual record: ai-coding-wiki `korean-overlay-gemini` was started on 10-21 09:52, and the first turn was `provider: claude`. The screen that was running was build `5c87441`, which included up to #198. #196 fixed it so that only the selection of "a state where no task is selected" (`selected === ''`) is used for the start. However, the same empty agent slot ("[Start] of specification ... sends the first turn") appears in other states as well. A state where a specification card before start is selected. Since it is stored in the key of that specification, it was not used when starting another card. A state where the selection remains pointing to a disappeared task. ...

Source. PR #199 · `LittleBitAI/fix-start-model-empty-pane`
