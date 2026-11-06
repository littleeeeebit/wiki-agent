---
scope: project
severity: preference
triggers: []
domain: ''
title: "Automatic preparation of mobile external connection and DNS failure recovery"
pr: 89
merged: 2026-10-07
branch: "LittleBitAI/diagnose-mobile-cloudflared-timeout"
---

# Automatic preparation of mobile external connection and DNS failure recovery

What. Quick Tunnel is created on the team member's PC, but the local DNS cannot find the `trycloudflare.com` address, so the mobile connection remains in a preparing state. The existing flow also required a separate cloudflared installation and an additional pairing click.

Why. Automatically prepare the verified official cloudflared runtime in the app-specific cache. Fix the version and SHA256, and isolate it from existing user settings. - If normal DNS lookup fails, verify the readiness of the public address via verified HTTPS DNS lookup within the app. Do not change the operating system DNS or turn off TLS verification. - If QUIC connection fails, automatically attempt HTTP/2, and handle cancellations, stale responses, and process terminations. - Display the readiness status and pairing QR with a single external connection button. It can be used with a mobile browser without an APK. - Scope of impact Python check: 97 passed. Includes additional HTTP error regression checks, 15 mobile checks passed. …

Source. PR #89 · `LittleBitAI/diagnose-mobile-cloudflared-timeout`
