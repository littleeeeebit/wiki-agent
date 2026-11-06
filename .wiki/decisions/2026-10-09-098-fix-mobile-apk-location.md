---
scope: project
severity: preference
triggers: []
domain: ''
title: "Exclude mobile installation APK from post-merge cleanup (P0)"
pr: 98
merged: 2026-10-09
branch: "fix/mobile-apk-location"
---

# Exclude mobile installation APK from post-merge cleanup (P0)

What. The Android app could not be installed. The installation QR disappeared, and `/mobile-install` and `/mobile-install.apk` returned 404. - Existing apps were also not connected.

Why. The APK provided by the server was at `artifacts/wiki-agent.apk`. `.wiki/adapter.toml` designates the entire `artifacts/` as `scratch_dirs`, so the post-merge cleanup (`rm -rf artifacts`) deleted the APK as well. I confirmed it was `apk_available:false` on the running server. - The fact that the existing app is not connected is not a code defect. As the server restarted at 10-08 22:17, the temporary trycloudflare address changed, and the app is storing the old address. It can be recovered by re-pairing via "Scan Connection QR" in the app's ⋮ menu. - `tool/main/mobile.py` and `tool/build_android. …

Source. PR #98 · `fix/mobile-apk-location`
