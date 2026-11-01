---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fixes the issue where the review shadow loops endlessly due to masked hashes."
pr: 215
merged: 2026-10-22
branch: "LittleBitAI/fix-review-shadow-stale-loop"
---

# Fixes the issue where the review shadow loops endlessly due to masked hashes.

What. The review loop for ai-coding-wiki PR #158 was stuck at `리뷰 대기`, never sent to the reviewer, and left no reason for the halt. In reality, it was not stuck but was repeatedly running the same step. After 16:07 `계속`, 128 shadow observations had accumulated, and all of them were `stale / input_changed`.

Why. 1. The hash was masked. The shadow context (`shadow_context`) passes the entire state through `verification.sanitize` to mask secret values. The values to be masked include all values from the worktree `.env`, and ai-coding-wiki `.env` contains short values like `RETRIEVAL_SUFFICIENCY_FLOOR=3` and `JUDGE_MAX_CONCURRENT=6`. Therefore, the base hash `c1d10ba877a318617e…` was changed to `c1d10ba877a[redacted]18[redacted]17e…`. 2. `observe_shadow` compares the real hash read again from Git with this masked copy, so it is different every time. …

Source. PR #215 · `LittleBitAI/fix-review-shadow-stale-loop`
