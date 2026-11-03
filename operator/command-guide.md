---
scope: operator
severity: contract
repeat: rule
triggers: ["어떤\\s*명령", "무슨\\s*명령", "파일\\s*(을|를)?\\s*찾", "\\bfd\\b", "\\bjq\\b", "json\\s*(을|를)?\\s*(읽|파싱|뽑)", "playwright", "브라우저\\s*(시나리오|테스트)", "명령\\s*안내"]
slots: []
sources: []
sources_withheld: true
links: [edit-files-as-diffs, run-inside-this-session]
---

# Which command for which work

Rule. Start with the command this page names for the work. The hooks refuse
or repair the rest, and each refusal costs a turn.

| Work | Command |
| --- | --- |
| Find files by name | `fd <pattern>`, or `rg --files` |
| Search text | `rg` |
| Read a file | the host's Read tool |
| Change a file git tracks | Edit on Claude, `apply_patch` on Codex (`edit-files-as-diffs`) |
| The same substitution in three or more files | one `sed -i` |
| Write a new file, a log or scratch | Write, or a shell redirect |
| Read or pick from JSON | `jq` |
| Run tests | the affected test files, `run_in_background` when long; a filter goes after `\| tee <log>` |
| A browser scenario | a Playwright script (`playwright.sync_api`), as `web/tests/task_browser.py` does |
| The person's own Chrome, signed in | the Chrome extension tools, only when asked |
| Windows-only work (registry, services, scheduled tasks, processes) | `pwsh` 7 (`run-inside-this-session`) |
| Something only the web knows | WebSearch, then WebFetch on what it found |

`fd` and `jq` are installed by `setup_agents.py --global` with winget, or
from their pinned release files when winget does not finish in three
minutes. When a command needs one that this session cannot find, the hook
runs it from where it was put: a session keeps the PATH it started with. When
the tool is not installed at all, the hook starts that install in the
background and says so; meanwhile use `rg --files` or Python's `json`.

Why. The audit of 2026-11-03 counted about 3,200 refused calls in three weeks
(`docs/plans/agent-commands/0-overview.md`). Most of them were the agent
starting with a command a rule later turned away. A guide read at the start
costs nothing; a refusal costs a turn every time.
