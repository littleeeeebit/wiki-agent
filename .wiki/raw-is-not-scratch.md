---
scope: project
severity: landmine
repeat: rule
triggers: ["artifacts", "scratch", "raw/", "정리", "청소", "지우", "삭제", "rm -rf", "APK", "apk", "머지 후"]
slots: []
sources: [046afc8 (#98), docs/mobile.md#L133, tool/test_mobile.py#L30, tool/main/mobile.py#L37]
links: []
---

# `artifacts/` is scratch and may be wiped; anything the program serves or keeps lives in `raw/`

Rule. Put nothing the running program reads back into `artifacts/`. After a
merge, cleanup deletes the `scratch_dirs` listed in `.wiki/adapter.toml`
(`artifacts/`), so anything there is gone. State, served files and keys go
under `raw/`: the APK and AAB in `raw/android/`, the signing key in
`raw/android-signing/`. Never delete `raw/` or a folder in it to "clean up";
it holds every task spec, conversation, review cell and the release
keystore. A new output that must outlive a merge goes under `raw/`, and its
path must not fall under any `scratch_dirs` entry.

Why. On 2026-10-09 the after-merge cleanup ran `rm -rf artifacts`. The
release APK lived there, so `/api/mobile/status` reported
`apk_available=false`, the install QR disappeared and `/mobile-install`
returned 404 (commit 046afc8, #98; `docs/mobile.md:133-136`). The build now
writes to `raw/android/` (`tool/main/mobile.py:37`), and
`tool/test_mobile.py:30` fails if the APK path falls under `scratch_dirs`.

What goes wrong. A served file quietly disappears at the next merge.
Deleting `raw/` loses the tasks, and losing the keystore means installed
phones can no longer be upgraded in place.
