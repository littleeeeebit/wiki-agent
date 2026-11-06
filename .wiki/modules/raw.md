---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# raw/ — the hub's local runtime state: tasks, conversations, logs, caches, build outputs

## What it is for

`raw/` is where the running program keeps its state on this machine. Git
ignores everything in it except `raw/.gitkeep` (`.gitignore:15-16`). It is
the hub's folder: a connected repository's task records live here too,
keyed by repository name. For example, a spec is
`raw/specs/<repo>/<id>.json` in the hub (`tool/main/specs.py:3`,
`tool/main/specs.py:103`). Nothing in it is distributed
(`docs/publishing.md:28-31`, `docs/publishing.md:55-58`).

## What lives where, and who writes it

| Path | Owner |
| --- | --- |
| `raw/specs/<repo>/` | Task specifications (`tool/main/specs.py:45`). Dropped ones are set aside in `dropped/` (`tool/main/specs.py:933`) |
| `raw/gate-times.jsonl` | Per-repository gate run times for the ETA (`tool/main/specs.py:162`) |
| `raw/server.lock` | One server per hub (`tool/main/runtime.py:159-170`, taken in `tool/main/app.py:67`) |
| `raw/chat/`, `raw/corrections.jsonl` | Wiki conversations and corrections (`tool/main/query.py:40-41`, `tool/chat_post.py:11`) |
| `raw/work/` | Work-session records (`tool/main/work.py:40`) |
| `raw/review/` | Review cells per repository and PR (`tool/main/loop.py:34`; cleanup removes only that PR's folder, `docs/development.md:315`) |
| `raw/refactor/runs/` | Refactor survey runs (`tool/main/refactor.py:31`, `tool/refactor_profile.py:38`) |
| `raw/actions/` | Jev action-decision logs (`tool/main/decisions.py:63`) |
| `raw/jev/settings.json`, `raw/knowledge/` | Jev settings and knowledge runs, beside the hub `.env` (`tool/decision/__init__.py:86`, `tool/main/knowledge.py:3401`) |
| `raw/connect/` | Connect-flow records and hook probes (`tool/main/connect.py:51`, `tool/hook.py:148`) |
| `raw/improvement/` | Self-improvement store (`tool/improvement.py:26`). Excluded from search (`tool/search/sources.py:99`) |
| `raw/update-later/` | Deferred app updates (`tool/main/update.py:57`) |
| `raw/errors.jsonl` (+ `.1`–`.3`) | Error log, rotated at 2 MB with three backups (`tool/common/errorlog.py:13`, `docs/development.md:330-338`) |
| `raw/main.log`, `raw/main.err.log` | The Tauri window's sidecar output (`web/src-tauri/src/main.rs:272`, `main.rs:70`) |
| `raw/translate-cache.sqlite3` | Translation cache, unless `TRANSLATE_CACHE` overrides it (`tool/translate/__init__.py:118`) |
| `raw/census-*.jsonl` | Map census input (`tool/graph.py:136`, `tool/graph.py:293`) |
| `raw/android/`, `raw/android-signing/`, `raw/android-sdk/`, `raw/jdk-17/` | APK outputs, release keystore and local toolchain (`tool/main/mobile.py:37`, `android/app/build.gradle:20`, `tool/build_android.ps1:6-8`) |
| `raw/mobile-runtime/` | The mobile transport runtime (`tool/main/mobile.py:161`) |
| `raw/eval/` | Evaluation run outputs (`tool/eval/baseline.py:42`, `laya/stage1.py:26`) |

## Depends on, depended on by

It depends on nothing. Almost every workflow in `tool/main/` depends on it
(the table above). Hook diagnostics are the exception: they go to
`%LOCALAPPDATA%/wiki-hook-diagnostics`, not here
(`tool/hook_diagnostics.py:22`).

## What trips a newcomer

- `raw/` is not scratch. Deleting it loses every task, conversation, review
  cell, the Android signing key and the served APK. Scratch is `artifacts/`
  (`scratch_dirs` in `.wiki/adapter.toml`). The APK once lived there and was
  deleted by cleanup (`docs/mobile.md:133-136`).
- Losing `raw/android-signing/release.p12` means installed phones cannot
  upgrade in place (`android/app/build.gradle:18-27`).
- Paths are computed from the hub checkout, so a second clone of the hub has
  its own `raw/` and its own tasks (`tool/main/channels.py:17`,
  `tool/main/specs.py:45`).
- Never force-add anything from here (`docs/publishing.md:28-31`). Some
  files quote production prompts and states.
- Tests never touch the real `raw/`. `tool/conftest.py:29-50` points the
  home, the translation cache and the Jev and Langfuse keys at temporary
  places.
