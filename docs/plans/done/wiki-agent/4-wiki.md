# Step 4 — `wiki` Independence

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. `wiki` runs without translation — it takes the question exactly as a human typed it, and returns the retrieved page exactly as it is written. The root module is called only by `__all__`. It is subject to the same checks as `translate` in Step 3.

## Does it run without translation

It already does. Step 2 split `inject.py` and left the translation in the hook entry point, and `pipeline_imports` blocks the `translate` import of `wiki`. This step only verifies that with the actual path.

| Path | Translation off (`TRANSLATE_MONTHLY_USD=0`, discard cache) |
| --- | --- |
| Query — `inject.py` hook | Five rule sheets were injected. Only the English version is missing |
| Graph — `graph.py` | Does not call translation from the start. `graph.json` appears |
| `import wiki` | `translate` is not in `sys.modules` |

## Public entry points

It is the `__all__` of `tool/wiki/__init__.py`. Since there are two types of callers, there are two groups of names.

| Group | Name | Caller |
| --- | --- | --- |
| Query — What does the utterance hit | `pages`, `match_pages`, `render_parts`, `rule_index`, `source_map`, `label`, `budget`, `RULE_BUDGET`, `REPO_BUDGET` | `inject`, `chat`, `trigger_audit` |
| Page format — How to read the page | `WIKI`, `SCOPES`, `front_matter`, `metadata_errors`, `links_of`, `resolve`, `hub_pages`, `project_pages`, `INJECTABLE`, `SLOT`, `adapter_path`, `slots_for` | `lint`, `graph`, `apply`, `repo_lint`, `repo_graph`, `session_state` |

I did not reduce them to five names like `translate`. The root tools actually read the page format, and to hide those names, a new function would have to be created for each tool. I wrote the names currently in use as they are, and things outside the list (`fit`, `shrink`, `knowledge`, `digest`, `fill`, `project_wiki`, `MAX_DECISIONS`) are changed internally as needed.

Changes made.

- Changed `wikilib.pages` to `hub_pages`. It overlaps with `match.pages`, so both cannot be public. The caller is only `lint`
- `git_ok` was moved to `lint.py`. The caller is only `lint` and it is about git, not the wiki
- Nine root modules use `from wiki import` instead of `from wiki.match import` and `from wiki.wikilib import`. Tests look at the internals as they are

## Answer event contract

`wiki` does not create the answer sentence. The sentence is created by `agent` as CLI, and the main handles weaving the two.

| Who | What |
| --- | --- |
| `wiki` | Question → List of retrieved page `(severity, body, path)`. `label(path)` is the name |
| `agent` | Answer sentence — `delta`·`tool`·`done`·`error` event |
| Main | Loads `{"kind": "hits", "text": "", "pages": [이름, ...]}` in front of the answer stream |
| Screen | Reads the `file:line` of the answer as a citation and opens it with `/api/file` (`Answer.tsx`) |

The `hits` shape is what `Ev` of `chat.py` and `web/src/lib/api.ts` already use. No new type was created. The new main in Step 6 loads the same shape. If severity or body text becomes necessary for the screen, I will attach it next to `pages` then.

## Holes postponed from PR #4

`import tool.translate` binds the name `tool`, not `translate`. `pipeline_surface` was only looking at `translate`, so `tool.translate._ask()` passed.

Fixes. I collected the names bound by `import tool` or `import tool.<무엇>` separately, and also checked `<그 이름>.<파이프라인>.<속성>` against `__all__`. `t.translate._ask()` after `import tool as t` is also caught together.

The second P2 of the same PR (preemption is not the upper limit of output tokens) was not included in this step because it involves changing the request shape and cache of `translate`.

## Things not included

| What | Why |
| --- | --- |
| `graph.py` to `wiki/` | `apply.runs` reads the hook installation status. I left it as a root tool as per the judgment in Step 2 |
| Bundling queries into one function | `inject` must insert translation between matching and rendering. If bundled, it would have to be split again |

## Verification

| Check | Result |
| --- | --- |
| `pytest tool/` | 285 passed. Same as before the change |
| `python tool/lint.py --check` | Exit 0. If I plant `from wiki.match import fit` in `chat.py`, `tool.wiki.fit` after `import tool.wiki`, and `wiki.shrink` after `import wiki`, it results in exit 1 and `공개 진입점` set |
| `python tool/test_lint.py` | Two new cases (`import tool.translate`, `import tool as t`) are red. If I run it with `lint.py` before fixing, both are green, so the check sees this modification. Public names after `import tool.translate` are green |
| Direct execution script | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` exit 0, `ruff check tool` passed |
| Hook actual path | `git reset --hard 로 되돌려줘` in `inject.py` with translation off — five rule sheets injected. `session_state.py --project .` exit 0 |
| Tool | `graph.py`, `trigger_audit.py --help`, `chat.py --check` passed |