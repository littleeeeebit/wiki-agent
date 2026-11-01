---
scope: project
severity: preference
triggers: []
domain: ''
title: "Automatic Python path forwarding during review execution"
pr: 96
merged: 2026-10-08
branch: "LittleBitAI/fix-review-loop-path"
---

# Automatic Python path forwarding during review execution

What. Review sessions and gates could select a different Python than the app server, requiring manual PATH correction. Now, the directory of the running `sys.executable` is automatically forwarded to the front of the subprocess PATH. It does not hardcode user paths or Conda environment names, and preserves the global environment and forwarded environment variable configurations. It also includes the Python executable in gate environment identification.

Why. In the subsequent scope approved by the user, the actual SQLite cache for translation tests was prepared before the request time budget, and the missing web build was generated normally. The timeout and cache write completion policies for actual translations were not changed. The exact cause of past intermittent failures has not been confirmed. The latest main (PR #95) has been integrated into this feature branch, and process ownership, cancellation, and cleanup handling have been preserved. The verification HEAD is `da906d2e707ba61dc4d3d4add5542c178363aef0`, and the past failure records below are preserved. The user approved the actual merge on the premise of allowing independent reviews and the final gate of the exact HEAD that succeeded. After R12 approval, the final full verification of this HEAD passed, and the existing GPT-5. …

Source. PR #96 · `LittleBitAI/fix-review-loop-path`
