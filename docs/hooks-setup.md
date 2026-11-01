# Connecting the shared rules to your project

Installing the chat and installing the hooks are separate. Hooks are commands
the CLI runs when it starts or receives a question. They read the shared
wiki's tools and hand the CLI the rules and documents that fit the project.

People read the Korean version of these steps in `overview/install.html`
(section B, "앱 없이 명령으로 붙이기"). A change to the steps here changes that
page in the same pull request.

## 1. Prepare the wiki

First, sign in to the CLIs you need with your own account, following the
[chat install guide](chat-setup.md). If you do not need the chat screen,
preparing `python -m pip install -r <wiki path>/requirements-hooks.txt` in a
Python 3.11 or later environment is enough. The `python` in the commands below
has to be the interpreter those packages are installed into. The packages the
hooks use are listed in that one file, and `setup_agents` confirms them before
installing.

The wiki uses a clean git checkout at a stable path. The folder name is free.
Paths with spaces and Korean characters are supported, but the current
installer refuses a path containing a quote, a dollar, a backtick or a
newline. On Windows, Claude hooks need Git for Windows' Git Bash and Codex
hooks need PowerShell.

### The search daemon — optional

`python tool/search "<query>" --project <repo>` prints the sections that
match, with the `path:line` each starts at. It asks a small local daemon on
`127.0.0.1:8791`, starts it when it is not there, and the daemon stops after
three idle hours; nothing needs to be
run by hand. No hook asks it what to inject: the triggers stay the only
authority there. Native lifecycle hooks run independently of search and do
not notify a terminal keepalive service.

For the vector half, install `python -m pip install -r <wiki path>/requirements-search.txt`
into the same Python. Without it the daemon ranks with BM25 alone. The model
(about 120 MB) downloads on first start into `~/.cache/wiki-agent/`, next to
the state file `searchd.json` and the vector cache. `WIKI_SEARCH=off` keeps
every caller from reaching the daemon.

## 2. Configure the project

Create `.wiki/adapter.toml` in the target project and replace the example
below with that project's real check commands. `adapters/example.toml` is a
fictional example used by the checks, not a real project's settings.

```toml
agents = ["claude", "codex"]

[slots]
review_dir = "artifacts/review"
gate_cmd = "python -m pytest"
live_cmd = "confirm the real events in a new session"
server_stop = "Ctrl+C in the terminal that started it"
scratch_dirs = "artifacts/"
```

A `[checks]` table registers checks beyond the gate, each by an id with a
command and what it examines. Once a work turn's checks have passed, Jev may
pick one more whose description covers the files the change touched, and the
server runs it before the pull request goes up; a failure holds the pull
request. Only registered commands run.

A check's optional `paths` lists the globs it covers (`*` also crosses `/`).
While a pull request is repaired, each round runs only the checks whose
`paths` match the files changed since the merge base, both names of a rename
and deleted files included. A file no check covers, a shared configuration
file (`conftest.py`, `requirements*.txt`, `pyproject.toml`, the adapter and
the like), a `paths` that is not a list of strings, or no merge base runs the
whole `gate_cmd` instead. The whole `gate_cmd` also runs once more on the
exact commit the review allowed, and `[머지]` waits for that result; a new
commit needs a new one. Without `paths`, every round runs the gate as before.

```toml
[checks]
lint = { cmd = "python tool/lint.py --check", about = "checks the wiki pages and plans under docs/ and the hub", paths = ["docs/**", "operator/**", "craft/**", "*.md"] }
ruff = { cmd = "python -m ruff check tool", about = "lints the Python under tool/" }

[checks.loop]
cmd = "python -m pytest -q tool/test_loop.py tool/test_specs.py"
about = "review state, publication and merge invariants"
paths = ["tool/main/loop.py", "tool/main/specs.py", "tool/prompts/review-*.md"]
```

Native lifecycle hooks own session context, rule injection, tool checks and
Stop reconciliation. `host_boundary.py` redirects obsolete desktop CLI calls to
the app's task and review workflow. Reinstalling removes this wiki's retired
`keepalive.py` entries; the search daemon never sends keystrokes or paid idle
turns. The old `keep_alive` adapter value is ignored.

### The effect guard

`tool/guard.py` is a PreToolUse hook on both hosts: Claude runs it as its own
entry, Codex runs it inside `codex_pretool.py` after `host_boundary.py` and the
pages' `enforce.deny` rules. It judges only sessions the app started
(`WIKI_AGENT_MANAGED=1`) in a checkout with an adapter; everywhere else it
passes.

It does not match command patterns. A shell command is parsed into a tree
(`;`, `&&`, `||`, `|`, `$(...)`, backticks, redirections, here documents, and
the strings handed to `bash -c`, `sh -c`, `pwsh -Command`, `powershell
-Command` and `cmd /c`, parsed again up to eight levels deep) and reduced to
effects: `read`, `write(path)`, `delete(path)`, `spawn(executable)`,
`network(host)`, and `top(reason)` for what cannot be decided before it runs —
`eval`, `source`, `-EncodedCommand`, a variable, substitution or glob in a
written path, a script file, an unclosed quote, nesting past the limit. An
Edit, Write, MultiEdit, NotebookEdit or apply_patch call is a `write` (or
`delete`) of its resolved target. Reads, and tools that neither run a shell nor
edit a file, are not restricted. The call passes when every effect lies inside
the task's grant, computed again for each call:

| Effect | Granted when |
|---|---|
| `write`, `delete` | the path resolves (links, junctions and `..` followed) inside a write root: the checkout, or only the spec's `artifact_root` for a plan task; the temporary directories (`TEMP`, `TMP`, Python's); the session's `memory` folder; each `[guard] write_roots` entry, relative to the checkout |
| — never | `.git`, `.claude`, `.codex`, `.wiki/adapter.toml` and `verification.toml` of the checkout and its main clone, and deleting any folder that holds them |
| `spawn` | the executable is one that `gate_cmd`, `verification.toml`'s round and local commands, a `[checks]` command or `python tool/search` runs, or is listed in `[guard] executables`. `git` and `gh` are modelled by verb: a known verb is its writes and its remote's host, an unknown one is `top` |
| `network` | the host is the loopback, a configured git remote's host, or listed in `[guard] network_hosts` |
| `top` | never |

The optional adapter table adds to the grant and never names a command:

```toml
[guard]
write_roots = ["../shared-fixtures"]
network_hosts = ["pypi.org"]
executables = ["ruff", "npx"]
```

A denial lists each effect outside the grant, why, and what would open it,
then the grant itself. The person reads it, so it stays Korean:

```
위키 효과 검사가 이 호출을 막았다 — 작업에 허가된 효과 밖이다.
- spawn(cargo): 허가된 실행 파일이 아니다 — `.wiki/adapter.toml` 의 `[guard] executables` 에 `cargo` 를 더하면 열린다
- delete(C:/work/other): 쓰기 루트 밖이다 — `.wiki/adapter.toml` 의 `[guard] write_roots` 에 이 경로를 담은 디렉터리를 더하면 열린다
- top(variable in a write path): 판단할 수 없는 형태다(variable in a write path) — 어떤 허가로도 열리지 않으니, 리터럴 경로와 직접 실행하는 명령으로 풀어 쓴다
허가: 쓰기 루트 C:\work\repo, C:\Users\me\AppData\Local\Temp · 실행 파일 npm, python · 호스트 127.0.0.1, github.com
```

A `top` is answered by writing the command out: a literal path instead of
`$TEMP/x`, the program itself instead of `eval` or a script file. A variable
the same command assigns once, before use and outside a conditional, counts
as its literal. The guard's limits: what a granted interpreter (`python`,
`npm`) does inside its own process is not modelled, and the hub's own
`tool/` is inside its checkout's write root. Any exception in the hook passes
the call (`craft/hooks-fail-open`).

Run `git rev-parse HEAD` in the wiki folder and store the resulting
40-character value as a single line in the **target project's**
`.wiki/wiki-revision`. Use UTF-8 without BOM and LF. That value is how the
team confirms everyone is on the same tool version.

Connecting Codex needs the following in the target project's
`.codex/config.toml`. If the file exists, do not overwrite it — review and add
just this entry.

```toml
[features]
hooks = true
```

Whether a project can be trusted is the user's judgement, confirmed through
the CLI's trust settings and `/hooks`. The installer does not decide trust or
hook approval on your behalf.

## 3. Install and reinstall

Run these from the shared wiki folder. The paths are examples; point them at
the real target project.

```powershell
python tool/setup_agents.py --project "../example-project" --agent both
python tool/setup_agents.py --project "../example-project" --agent both --check
```

Using one CLI only, choose `--agent claude` or `--agent codex`. The CLI you
use has to be findable on that terminal's PATH. Settings are read from that
checkout directly and never copied into the hub. A reinstall preserves
settings created by other tools and the settings of any other host already
installed.

Having moved the project or the wiki, install again with the same command.
Raising the wiki version means reviewing the changes, replacing
`.wiki/wiki-revision` with the new value, and reinstalling.
`--allow-dirty-wiki` is for development checks and does not substitute for
verifying a released version.

## 3a. Install once for the whole machine

The per-project install above writes gitignored files into one checkout, with
that checkout's absolute path in every command. A new worktree has none of
them, and Codex drops its trust in a hook whenever the entry changes (a new
timeout is enough). Both look like "the update broke the hooks".

The user-level install writes the hooks into `~/.claude/settings.json`
and the default and explicitly selected Codex homes (`~/.codex` and
`CODEX_HOME`). Other applications' accounts are never scanned. Every command calls
`tool/hook.py`, which works out the project from the session's directory: a
worktree is served by its main clone's `.wiki/adapter.toml`, and a directory
with no adapter passes silently. Run it from the stable wiki checkout:

```powershell
python tool/setup_agents.py --global --trust-codex --project "../example-project"
```

The pages' deny rules are not written into the user-level `permissions.deny`
— there they would bind every repository on the machine. Name every attached
repository's main clone with `--project`: it gets them in its own
`permissions.deny`, where the host enforces them even if a hook fails. The
health check (`tool/repo_lint.py`, run by the Stop hook) reports an attached
clone that has not been named yet. Worktrees are the one place left to
`tool/deny.py` behind the dispatcher, which passes when the hook fails or
times out — the price of a worktree needing no install at all.

`--project` (repeatable) removes that checkout's old per-project hooks, which
would otherwise keep the job — the dispatcher steps aside for them.
`--trust-codex` records trust only for commands that run this checkout's
`hook.py`, through Codex's own config writer; leave it out to review them in
Codex's `/hooks` instead.

After updating Claude Code, Codex or this wiki, run the check. It re-reads
every user-level file, reports Codex hooks waiting for trust, and runs the
SessionStart hook once from each `--project` (or the current folder):

```powershell
python tool/setup_agents.py --global --check
```

## 4. Confirm on the real host

A successful `--check` is a settings-wiring check. That is different from the
automatic events actually succeeding. Open a new Claude Code session and a new
Codex session, and confirm the following.

- That the project documents and current state are delivered at session start
- That a question about a rule delivers that rule
- That the permissions and block rules needed for editing and checking behave
  as intended
- That the options question tool is actually offered on this host, in this mode

`trajectory.jsonl` is a trace of the injector running. A manual invocation or
a display-time query can create it too, so that file alone is not treated as
having verified the host's automatic events. If the question tool is not
offered, or is refused, record that result as it is.

A project's `.wiki` will accumulate real decisions and analysis records over
time. Separate what the team shares from personal records and set that
project's git exclusions accordingly.
