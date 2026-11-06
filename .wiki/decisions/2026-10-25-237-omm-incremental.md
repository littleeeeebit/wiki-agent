---
scope: project
severity: preference
triggers: []
domain: ''
title: "When merging, the structure document (.omm) refresh reads only the parts changed by the PR and runs with the model selected in the settings."
pr: 237
merged: 2026-10-25
branch: "omm-incremental"
---

# When merging, the structure document (.omm) refresh reads only the parts changed by the PR and runs with the model selected in the settings.

What. Structure document refresh: In merge preparation, it reads only the files and diffs changed by the PR after the merge-base, and provides only the `.omm` nodes with those paths to the model. The model returns only the nodes to be changed or deleted, and if there is nothing to change, it leaves `.omm` as is. If the answer does not fit the format, it asks again once with the reason attached. - Model fixed in settings: Select "Structure document refresh model" and inference intensity in Settings → Review (`/api/architecture/settings`). …

Why. Every time [Merge] is clicked, the `.omm` refresh analyzed the entire repository again as a task model, consuming many tokens (about 12 minutes in #257, followed by re-review and final gate).

Source. PR #237 · `omm-incremental`
