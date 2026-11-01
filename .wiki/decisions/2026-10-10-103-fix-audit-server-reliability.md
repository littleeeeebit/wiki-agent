---
scope: project
severity: preference
triggers: []
domain: ''
title: "External audit follow-up: POSIX process tree termination, configuration file concurrent write lock, Ruff pinning"
pr: 103
merged: 2026-10-10
branch: "fix/audit-server-reliability"
---

# External audit follow-up: POSIX process tree termination, configuration file concurrent write lock, Ruff pinning

What. This is the server stability bundle (R2·R5·Ruff pinning) among the remaining items from the external audit (2026-10-10). It is a follow-up to #101.

Why. R2: On Windows, a job terminates the entire child tree, but on POSIX, Stop only kills direct children, so background commands launched by the CLI could continue to run. The `ponytail:` comment in the code already noted this limitation. `runtime`·`refactor_profile`·`improvement` already started as a group and directly `killpg` when there was no job, so now the same cleanup is unified into the job path. R5: If the pre-fix logic was run on two threads simultaneously, one side's value would disappear or temporary file replacement would fail 20 out of 20 times. Desktop and mobile can write to the same file. Since the server is a single process owner (`server_owner`), an in-process lock is sufficient. …

Source. PR #103 · `fix/audit-server-reliability`
