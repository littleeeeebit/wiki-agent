---
scope: project
severity: landmine
repeat: rule
triggers: ["conftest", "GEMINI_API_KEY", "TYPESAFE_API_KEY", "LANGFUSE", "Langfuse", "\\.env", "API ?키", "테스트.{0,20}(키|번역|Jev)", "live (Gemini|request)"]
slots: []
sources: [6040582 (#29), tool/conftest.py#L35, fc3a480 (#32), tool/conftest.py#L43]
links: []
---

# Tests never read the machine's keys or start a real daemon, model or host

Rule. Every test runs with `tool/conftest.py`'s environment: an empty
`WIKI_USER_HOME`, `WIKI_SEARCH=off`, a missing `TRANSLATE_ENV`, a scratch
`TRANSLATE_CACHE`, a missing `JEV_ENV`, no Gemini, TypeSafe, Jev or
Langfuse variables, and `WIKI_JEV_FALLBACK=off`. Never undo these in
`tool/conftest.py`. A test that needs a key writes its own env file and
points the variable at it; a test that needs a host or daemon stubs it.
Never read the hub's `.env` or the real `raw/` from a test.

Why. Opening a pull request translates it. With the checkout's `.env`
visible, every such test spent a live Gemini request (`tool/conftest.py:35-41`,
added in 6040582, #29). The hub's `.env` also holds a live TypeSafe key for
Jev (`tool/conftest.py:43-50`, fc3a480, #32). An uncertain verdict in
active mode would start a real host CLI (`tool/conftest.py:51-53`), and a
test that spawned the search daemon would download its model
(`tool/conftest.py:31-33`).

What goes wrong. The suite bills the user's API credit, sends traces to the
local Langfuse, and becomes flaky on network and quota.
