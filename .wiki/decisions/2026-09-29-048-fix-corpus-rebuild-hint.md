---
scope: project
severity: preference
triggers: []
domain: ''
title: "fix: The old list guide provides the command that actually runs"
pr: 48
merged: 2026-09-29
branch: "fix/corpus-rebuild-hint"
---

# fix: The old list guide provides the command that actually runs

What. The discovery of "old list" in `repo_lint` and the no-list guide in `repo_graph` told to run `tool/corpus.py --write`, but `corpus.py` requires `--project`, so that command failed as is. This was revealed while running it as guided during the cleanup after merging PR #47.

Why. `corpus.rebuild(repo)`: Create the command to rewrite the list right next to the CLI argument. The target repository path is included (`python tool/corpus.py --project "<저장소>" --write`, paths with spaces as one argument) - `repo_lint` in two places and `repo_graph` in one place all use it - `test_repo_lint`'s list test executes the command printed in the discovery exactly as printed, and then checks if lint is clean. If reverted to the old phrasing, this test fails …

Source. PR #48 · `fix/corpus-rebuild-hint`
