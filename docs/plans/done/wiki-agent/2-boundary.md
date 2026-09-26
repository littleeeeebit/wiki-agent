# Phase 2 — Boundary Check

The relationship between the overall design and the phases is in [Overview](0-overview.md)].

Goal. If pipeline nets each enter their own folders, and even a single import between folders occurs, `lint --check` turns red. The behavior does not change at all — move, split, and set up checks.

## Moving

| Current | After Moving | Name Used by Caller |
| --- | --- | --- |
| `tool/translate.py` | `tool/translate/__init__.py` | `import translate` as is |
| `tool/sessions.py` | `tool/workspace/sessions.py` | `from workspace import sessions` |
| `tool/chat_session.py` | `tool/agent/chat_session.py` | `from agent import chat_session` |
| `tool/chat_local.py` | `tool/agent/chat_local.py` | `from agent import chat_local` |
| `tool/wikilib.py` | `tool/wiki/wikilib.py` | `from wiki import wikilib` |
| Matching/Rendering of `tool/inject.py` | `tool/wiki/match.py` | `from wiki import match` |

Only `translate` is put into `__init__.py` as a whole. It is a single-module pipeline and has the same name, so the caller is not modified at all. For the rest, the module names are kept as is and placed inside folders. Gathering public entry points into one is done in phases 3~5.

`python tool/translate.py --check` becomes `python tool/translate --check`.
`tool/translate/__main__.py` is its entrance.

### The line splitting `inject.py`

There are three places in `inject.py` that call `translate` — `rendering`, `localised`, and the finishing `BUDGET` that they share. This and `main` remain in the hook entry point, and everything else goes to `wiki/match.py`.

| `wiki/match.py` | `tool/inject.py` (Hook Entry Point) |
| --- | --- |
| `pages`, `match_pages`, `render_parts`, `fit`, `shrink`, `knowledge`, `digest`, `rule_index`, `source_map`, `label`, `slots_for`, `budget`, `adapter_path`, `fill`, `project_wiki` | `main`, `rendering`, `localised`, `BUDGET`, `MAX_RENDERED`, `HANGUL` |

`inject.py` keeps its file name and location. This is because user-level hooks call `hook.py claude inject.py`, and the former project-level installation calls via the `tool/inject.py` path. Places that used matching functions by borrowing the name of `inject.py` (`apply`, `graph`, `trigger_audit`, `chat`, tests) are modified to call `wiki.match` directly. `inject.py` does not provide a re-export path — if that path exists, one cannot visually tell if the boundary is being crossed.

## What not to move

| What | Why |
| --- | --- |
| `hook.py` and hook scripts (`inject.py`, `session_state.py`, `sync.py`, etc.) | Installed hooks call by path and name |
| `graph.py` | It is a command-line tool that creates a wiki map. Only the result (`graph.json`) is read by the screen. Since it uses `apply.runs`, putting it in `wiki/` crosses the boundary |
| `chat.py`, `chat_channels.py`, `chat_post.py`, `mirror.py`, `transcript.py` | It is the main side. Rebuilt or deleted in phase 6 |
| Remaining `tool/*.py` | Hook, installation, check tools. Not a pipeline |

## Slack Deletion

Delete: `slack_brief.py`, `slack_post.cmd`, `prompts/slack-standup.md`,
Slack runner tests for `prompts/slack-retro.md`, `test_slack_brief.py`, `test_distribution.py`.
Move `repo_url` used by `chat.py` into `chat.py`. Also remove the Slack sections (`docs/publishing.md`,
`docs/development.md`, `docs/verification.md`) of the document.

## Check

`lint.pipeline_imports` reads all `.py` under `tool/wiki/`, `tool/translate/`, `tool/agent/`, `tool/workspace/`,
`tool/common/` as `ast`.

- Also counts lazy imports inside functions. There are many lazy imports in the current code
- Relative imports (`from .chat_local import ...`) pass because they are within the same package. Going up as deep as the folder containing the file reaches the `tool/` root, so it is treated the same as an absolute import (`from .. import translate`)
- Also looks at the `tool.` spelling (`from tool import translate`, `import tool.apply`) of the same module.
  If the repository root is in the path — as pytest is — it is also imported with this spelling
- If the first name is a different pipeline or a `tool/` root module, it is one discovery
- `common/` cannot be called by any pipeline or root module

The type of discovery is `파이프라인 경계`. It makes the exit code of `lint --check` red.

Check nets (`fragile_tools`, `fragile_io`, `broken_wraps`, `korean_prose`) that only looked at `tool/*.py` now look at subfolders as well. Otherwise, moved files are silently omitted from encoding checks.

### Test to see if it turns red

Create and plant a throwaway wiki in `test_lint.py`.

| What is planted | Expectation |
| --- | --- |
| `tool/wiki/a.py` is `import translate` | Red |
| `tool/wiki/a.py` is `from agent import chat_session` inside a function | Red |
| `tool/wiki/a.py` is root module `import apply` | Red |
| `tool/wiki/a.py` is `from tool import translate`, `import tool.apply`, `from tool.translate import x` | Red |
| `tool/wiki/a.py` is `from .. import translate` | Red |
| `tool/common/c.py` is `import wiki` | Red |
| `tool/wiki/a.py` is `from . import b`, `import re`, `from common import c` | Green |

And this repository itself is green — `lint --check` exit code 0.

## Verification

| Check | Result |
| --- | --- |
| `pytest tool/` | 274 passed. 281 before change, 7 Slack tests deleted |
| `python tool/lint.py --check` | Exit 0. If `import translate` is planted in `wiki/match.py`, exit 1 |
| `python tool/test_lint.py` | Eight boundary violations and subfolder Korean comments are red, allowed imports are green |
| Hook actual path | In a temporarily attached repository, `hook.py claude` to `inject.py`·`session_state.py`·`sync.py` — exit 0, injection and English version appear |
| `python tool/translate --check` | Output is the same as `translate.py --check` before the change |
| Server | `python tool/chat.py --check` passed. Asking `#위키` via browser could not be done because `web/dist` is missing |
| Review | Round 1 P1 one (`tool.` prefix bypass) fixed, no new discoveries in round 2 |
