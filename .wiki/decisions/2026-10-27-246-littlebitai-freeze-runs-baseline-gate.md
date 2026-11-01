---
scope: project
severity: preference
triggers: []
domain: ''
title: "Run a full repository check before fixing the behavior in step 1; if the fix test violates repository rules, it stops before fixing."
pr: 246
merged: 2026-10-27
branch: "LittleBitAI/freeze-runs-baseline-gate"
---

# Run a full repository check before fixing the behavior in step 1; if the fix test violates repository rules, it stops before fixing.

What. In step 1(`preserve`), just before the server fixes the behavior fix test, it runs the baseline gate (full repository check) used during the investigation one more time on the behavior fix commit (`tool/main/refactor_run.py`'s `regressed`). - If a new failure that was not in the baseline results during the investigation appears, it stops without fixing. At this time, it informs that the test can still be fixed. - If the name of the failed check cannot be known, it passes only when the exit code is the same as during the investigation. ...

Why. The step 1 card `preserve-repository-boundaries-1f9c4293` of ai-companion-voice-agent stopped at review preparation. 1. The behavior fix commit `d2563f3d` added 5 tests to `tests/integration/`. This repository has a rule test that requires declaring `pytest.mark.usefixtures("default_settings")` for every integration test, and all 5 were missing this declaration. 2. In the fix step, this issue was not visible because only the tests to be fixed were run. It was caught only during the final check. 3. The worker fixed it by adding a line of annotation to the 5 fixed tests. Then, the review preparation check reported that the fixed tests had changed and stopped. ...

Source. PR #246 · `LittleBitAI/freeze-runs-baseline-gate`
