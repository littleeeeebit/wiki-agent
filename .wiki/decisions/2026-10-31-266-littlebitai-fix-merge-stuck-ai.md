---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "If a merge preparation commit only changes the wiki index, it does not re-run the final verification that has already passed, and if the final verification fails, it is immediately displayed on the merge screen."
pr: 266
merged: 2026-10-31
branch: "LittleBitAI/fix-merge-stuck-ai"
---

# If a merge preparation commit only changes the wiki index, it does not re-run the final verification that has already passed, and if the final verification fails, it is immediately displayed on the merge screen.

What. Inheriting final verification (`tool/verify.py`'s `carried`): Descendant commits that differ only in the generated index (`.wiki/corpus.json`, `.wiki/graph.json`) from a commit that passed locally inherit that pass. This only applies when the base and verification conditions (signature) are the same. Independent reviews still run. Execution and pre-merge checks read the same receipt. Inheritances that do not run anything are not included in the gate remaining time estimation record.

Why. When I clicked [Merge] in ai-generation #60, it appeared to be stuck for several hours at step 6 of the merge process, "Preparation commit independent review/final gate in progress." Preparation commit `c732fa3` only changed one line of `.wiki/corpus.json`. Nevertheless, the 30-minute full test that had already passed in `d0c1aff` ran again from the beginning. The re-run test failed twice on time-sensitive tests under load (`test_comic_choice`, `test_comic_runner[16]`). Each time it failed, repair → review → 30-minute test were repeated. Meanwhile, the merge screen continued to show a spinner and "Will merge when finished." However, the structure was such that if a repair commit changed the code, that merge request would eventually be canceled.

Source. PR #266 · `LittleBitAI/fix-merge-stuck-ai`
