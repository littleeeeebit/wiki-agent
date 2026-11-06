---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "Add paired mobile companion, QR pairing and manual screen modes"
pr: 63
merged: 2026-10-02
branch: "feat/mobile-companion"
---

# Add paired mobile companion, QR pairing and manual screen modes

What. Add an opt-in browser companion: the PC owns execution and records; a paired phone connects through HTTPS from mobile data or another Wi-Fi network. …

Why. Scoped backend regressions after the review repair: 66 passed (`tool/test_mobile.py` and `tool/test_main.py`). - Final full gate, run once after review approval: `python -m pytest tool` — 1,322 passed, 1 skipped, 1 existing warning; exit 0 in 962.76 seconds. - Frontend build/lint, Ruff, wiki/repository lint, diff check and UTF-8-without-BOM checks passed; existing lint/build warnings remain. …

Source. PR #63 · `feat/mobile-companion`
