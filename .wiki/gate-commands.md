---
scope: project
severity: contract
repeat: rule
triggers: ["게이트", "gate", "ruff", "pytest", "최종 검증", "verify\\.py", "전체 ?(검사|테스트)", "테스트를? ?(돌|실행)", "CI"]
slots: []
sources: [d816dcd, verification.toml, conftest.py#L8, tool/verify.py#L60]
links: []
---

# This repository's checks: round `ruff tool laya`, affected test files, one final owner

Rule. During implementation and repair, run `python -m ruff check tool laya`
(the round command in `verification.toml`) and only the affected test files
or `-k` selections, for example `python -m pytest -q tool/test_verify.py`.
Do not run `pytest tool`, `pytest laya` or `pytest .`: the root
`conftest.py` refuses them outside the final owner (`conftest.py:8-13`,
`tool/verify.py:60-65`). Do not try to get around that refusal. The full
gate runs once, after review allows the candidate:
`python tool/verify.py final --base main`. It chooses hosted CI
(`verify.yml`) for a public repository with an active workflow, otherwise
one bounded local run of `verification.toml`'s `local` command. That
command covers ruff, `tool/lint.py --check`, `tool/debt.py check`, pytest
on `tool` and `laya`, web lint and build, and `web/tests/task_browser.py`.
`python tool/verify.py status --base main` only reads proof. After touching
`android/`, run `tool/build_android.ps1` yourself: no gate builds Android.

Why. Final verification was given one owner, CI or bounded local, so that
repairs do not start duplicate suites (d816dcd). The general rule is the
hub's `craft/verify-narrow-then-wide`. This page holds the commands.

What goes wrong. A whole suite started during a repair is refused, or it
competes with the owner's run on the same machine.
