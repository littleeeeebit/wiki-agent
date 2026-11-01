---
scope: project
severity: preference
triggers: []
domain: ''
title: "Android installation, mobile workspace, and real-time synchronization"
pr: 64
merged: 2026-10-03
branch: "feat/mobile-apk-landscape-sync"
---

# Android installation, mobile workspace, and real-time synchronization

What. Separate pairing and private API access by adding an installation QR/download page separate from the release-signed Android APK shell. - Provide explicit portrait and landscape screen modes without sensor or system auto-rotation detection. - Rebuild the mobile landscape mode into a compact task list/conversation/task workspace with folding and focus control features, without changing the desktop geometry. …

Why. 73 target backend tests passed. - Frontend build and lint passed, with lint maintaining existing warnings. - Mobile layout regression tests passed, including desktop geometry, drafts, touch targets, and selection state contrast. - 2 client browser tests via authenticated WSS passed: bidirectional, partial responses, manual reconnection, online recovery to active execution, clearing, and focus isolation. - APK 0.1.4: 4 release unit tests, Android lint, and release signing/alignment checks passed. The new native window policy source guard passed and failed independently reintroduced policy changes respectively. Installation and connection were verified before this review fix, and physical S21 rotation/keyboard and external link acceptance are maintained separately. …

Source. PR #64 · `feat/mobile-apk-landscape-sync`
