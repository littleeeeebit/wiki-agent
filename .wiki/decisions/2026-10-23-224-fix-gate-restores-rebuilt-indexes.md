---
scope: project
severity: preference
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "Prevent the index commit repair turn from running at the start of every review, and run the conda project's gate in that environment."
pr: 224
merged: 2026-10-23
branch: "fix/gate-restores-rebuilt-indexes"
---

# Prevent the index commit repair turn from running at the start of every review, and run the conda project's gate in that environment.

What. The `connect.guess()` in the connection phase only looked at `pytest.ini` and set the gate to `python -m pytest`. - On the server, `python` resolves to anaconda base, but base does not contain the dependencies for this project, including pytest-asyncio. - Therefore, all test modules resulted in an ERROR during the collection phase …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #224 · `fix/gate-restores-rebuilt-indexes`
