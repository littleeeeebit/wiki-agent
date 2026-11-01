---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# adapters/ — the hub's legacy per-project slot files, now one synthetic fixture

## What it is for

A hub rule page may contain slots such as `{gate_cmd}` or `{review_dir}`.
An adapter is the TOML file that gives one project's values for them
(`agents` and a `[slots]` table, `adapters/example.toml:2-8`). In the hub
there is only one tracked adapter, `adapters/example.toml`, and its first
line says it is a synthetic compatibility fixture: real projects keep their
own `.wiki/adapter.toml` (`adapters/example.toml:1`). Every other
`adapters/*.toml` is local and ignored by git (`.gitignore:20-21`).

## Entry points

There is no code here. These read the folder:

- `adapter_path` checks the checkout's `.wiki/adapter.toml` first. Only when
  that file is missing does it fall back to `adapters/<name>.toml` in the hub
  (`tool/wiki/match.py:258-265`). `slots_for` turns the result into a
  `{slot: value}` table (`tool/wiki/match.py:268-277`).
- The `inject.py` hook takes `--adapter <name>` for that fallback
  (`tool/inject.py:163`).
- `apply.py` warns when no adapter is found (`tool/apply.py:784-786`) and
  lists the slots still unfilled (`tool/apply.py:788-793`).
- `lint.py` loads every `adapters/*.toml` and compares slot values across
  them (`tool/lint.py:118-134`).
- The wiki map names the adapter each project uses
  (`tool/graph.py:213-214`).

## Depends on, depended on by

It depends on nothing. These depend on it:

- `tool/test_codex_hooks.py:19` runs the hooks with `--adapter example`.
- `tool/test_local_adapter.py:24` asserts that a checkout's own adapter wins
  over `example`.
- `tool/test_distribution.py:24` asserts `adapters/example.toml` stays
  tracked.

The per-repository file itself is owned by the connect flow
(`ADAPTER = ".wiki/adapter.toml"`, `tool/main/connect.py:55`), not by this
folder.

## What trips a newcomer

- Editing `adapters/example.toml` to configure a project does nothing for a
  connected checkout. That checkout's `.wiki/adapter.toml` wins
  (`tool/wiki/match.py:261-264`). Write slot values there.
- A missing slot is not an error at injection time. `fill` replaces only
  known names and leaves `{gate_cmd}` standing in the injected text
  (`tool/wiki/match.py:280-288`). `apply.py` is the only place that points
  it out (`tool/apply.py:785-786`).
- Lint reports `모순(슬롯)` when two adapters give a slot different values
  (`tool/lint.py:124-134`). That is normal: slots exist so each project can
  differ. The maintenance step drops this finding (`tool/main/maintenance.py:139`).
- `example.toml` is test input. Its values are deliberately generic, for
  example `gate_cmd = "python -m pytest tool"` (`adapters/example.toml:5`), and
  are not this repository's real settings, which live in `.wiki/adapter.toml`.
