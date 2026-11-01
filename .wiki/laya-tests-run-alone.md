---
scope: project
severity: contract
repeat: rule
triggers: ["pytest laya", "laya.{0,20}(test|테스트)", "라야.{0,20}테스트", "import laya", "laya/"]
slots: []
sources: [.github/workflows/verify.yml#L114, laya/README.md#L7, pytest.ini#L2]
links: []
---

# `laya/` is not a package; its tests run in their own serial pytest process

Rule. Run Laya's tests alone, from the repository root:
`python -m pytest laya/test_<x>.py -n 0`, never in the same pytest
invocation as `tool/` tests. Keep `laya/` modules importing each other by
bare name and never add `laya/__init__.py`: `import laya` must keep meaning
the installed model package. `laya/` may reach `tool/` only by appending it
to `sys.path`, as `laya/generate.py:57-59` does, not by importing
`tool.<x>`.

Why. Laya and the application have distinct bare module namespaces, so CI
runs them in separate jobs (`.github/workflows/verify.yml:114-115`).
`pytest.ini:2` collects only `tool` (`testpaths = tool`), and
`laya/README.md:7-9` states the bare-import convention.

What goes wrong. A combined run imports the wrong `gate`, `store` or
`kinds` module, or shadows the `laya` package, and fails in ways that look
like real regressions.
