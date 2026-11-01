---
scope: project
severity: preference
triggers: []
domain: ''
title: "Review loop: For large file diffs, keep only the headers in the instructions (1 MiB input limit)"
pr: 152
merged: 2026-10-15
branch: "fix/review-diff-large-files"
---

# Review loop: For large file diffs, keep only the headers in the instructions (1 MiB input limit)

What. In the `gh pr diff` attached to the review round instructions, if a file's diff exceeds 100,000 characters, that file will have its changed lines removed, leaving only the header (file name · blob hash).

Why. The review loop for rfg_rag PR #48 stopped at `멈춤 — 라운드 형식 / 리뷰 셀이 답하지 못했다 — 입력값이 최대 길이인 1048576자를 초과합니다`. The `instruction()` of `tool/main/loop.py` puts the entire `gh pr diff` output into the instructions as is. #48 contained two golden release data files (`eval/golden/test.jsonl` 2.12 million characters, `eval/golden/dev.jsonl` 870,000 characters), so the diff was 3,047,462 characters. It exceeded the 1 MiB limit for a single model input, and the review cell failed twice even after retries, causing the round to stop. Storing data files as raw bytes is the intention of #48, so the files cannot be reduced on the PR side.

Source. PR #152 · `fix/review-diff-large-files`
