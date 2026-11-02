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
passes. `tool/deny.py` matches each page's `Bash(...)` pattern against not only
the direct command but every command the guard's parser finds one level down,
so a block such as `git reset --hard` holds even inside `bash -c '…'`,
`pwsh -Command '…'` or `$(…)`.

It does not match command patterns. A shell command is parsed into a tree
(`;`, `&&`, `||`, `|`, `$(...)`, backticks, redirections, here documents, and
the strings handed to `bash -c`, `sh -c`, `pwsh -Command`, `powershell
-Command` and `cmd /c`, parsed again up to eight levels deep) and reduced to
effects: `read`, `write(path)`, `delete(path)`, `spawn(executable)`,
`network(host)`, and `top(reason)` for what cannot be decided before it runs —
`eval`, `source`, `-EncodedCommand`, a variable, substitution or glob in a
written path, a script file, an unclosed quote, nesting past the limit, more
than 32 possible working directories. An
Edit, Write, MultiEdit, NotebookEdit or apply_patch call is a `write` (or
`delete`) of its resolved target. A shell tool that names its own working
directory (`workdir` or `cwd` in its input) is judged from there; one that
cannot be read is `top`. Reads, and tools that neither run a shell nor
edit a file, are not restricted. The call passes when every effect lies inside
the task's grant, computed again for each call.

What a program does is read off a small closed table, never guessed. `TABLE`
in `tool/guard_models.py` lists each modelled program with the flags it
understands (`row(model, "-abc --long -o: --out:")`: one-letter flags, long
flags, and flags that take a value); `PS_ROWS` and `CMD_ROWS` in
`tool/guard_windows.py` do the same for PowerShell cmdlets and cmd.exe
builtins, and `tool/guard_git.py` for git and gh. The table holds only:

- read-only listing, reading and searching programs (`ls`, `cat`, `head`,
  `grep`, `rg`, `find` without actions, `diff`, `echo`, `Get-Content`,
  `Get-ChildItem`, `Select-String`, `Test-Path`, cmd's `type`, `dir`, `echo`);
- `cd` and `Set-Location` in the call's first statement only;
- git reads (`status`, `diff`, `log`, `show`, `rev-parse`, `rev-list`,
  `ls-files`, `ls-tree`, `check-ignore`, `merge-base`, `cat-file`, `describe`,
  `blame`, `grep`, `remote -v`, `remote get-url`, `config` that only gets or
  lists, `stash list`/`show`, `worktree list`, and `branch` that only lists);
  writes of the repository (`add`, `commit -m`/`-F`, `branch`, `checkout`,
  `switch`, `restore`, `merge`, `rebase` without `-i`/`-x`, `stash` without
  `-a`, `worktree add`/`remove`/`prune`); and the network (`fetch`, `pull`,
  and `push` with at most a branch) to a configured remote. A write of the
  repository is a write of the whole worktree root, wherever below it git
  runs. The network verbs must carry `-c http.followRedirects=false` and
  `--no-recurse-submodules`, so a redirect or a submodule's own remote cannot
  pick another host; a configuration that mentions `followRedirects` leaves
  the remotes unread. A path operand of
  `checkout`, `restore` or `stash push` is also a write of that path, and a
  pathspec pattern (`'*.toml'`, `:(top)`) is `top`; `worktree add` writes its
  folder and `worktree remove` deletes it, named only by a path absolute on
  the host (git matches a relative one, `C:/x` on POSIX included, as the tail
  of any registered worktree's path). `-C <dir>` moves where git runs, and
  `-c` takes only identity, whitespace and colour keys. `--git-dir`,
  `--work-tree`, `--exec-path`, `--config-env`, a `GIT_TRACE*` file and every
  other verb are `top`, and in a partial clone every git form is, since
  reading a missing object fetches it;
- gh `pr view`/`diff`/`checks`/`list`/`status`, `run view`/`watch`/`list`,
  `issue view`/`list` as reads, and `pr edit`/`comment`/`ready`/`merge` on the
  current branch's pull request only, each reaching the checkout's remote
  hosts. `-R`/`--repo`, `--web`, `--hostname`, `run view --log` and a URL
  operand are `top`, and so is setting `GH_REPO` in any form (PowerShell's
  one-word and compound assignments included). `pr create` (it may push the
  branch) and `pr merge --delete-branch` (it switches branches and pulls) run
  git with options the call cannot add, so they are `top`. `gh api` and curl
  have no row: the hosts they reach and what they change are not decided by
  their arguments alone, so they wait for the shell-semantics follow-up;
- plain `>`/`>>` redirection to a literal path, in every dialect. Only the
  host's devices are discarded: `/dev/null` and its kin as spelled where a
  POSIX layer serves them (not in native Windows PowerShell or cmd, where they
  are files under the drive root), `NUL` and `CON` on Windows in any case, and
  PowerShell's `$null` only unquoted.

A git row's arguments never select a program: `--show-signature` and a
`--format`/`--pretty` value with a `%G` field or a configured alias name are
`top`. What git starts from its own configuration (hooks, a text converter,
the verifier `log.showSignature` asks for) is part of the git row, since that
configuration is protected or outside the write roots. That holds only for the
checkout itself and the worktrees of the same repository (one configuration,
one set of hooks and remotes): git in any other repository (a nested one
included) is `top`, and no write or edit may lay down a `.git` anywhere. When
any configuration git reads may point `core.hooksPath` inside a write root,
every git write of the repository is denied, since git would run hooks the task
can edit. Only an absolute value on a line of its own is read as written; a
relative value (it depends on the worktree that runs it), another syntax, an
`[include]` or `extensions.worktreeConfig` counts as a writable folder.

A verb that moves the working tree between commits (`checkout`, `merge`,
`pull`, `rebase`, `stash pop`) writes whatever the repository's history holds,
protected files and links included. That history is trusted because the agent
cannot author such a commit within the table: no row lays down a link, and a
protected name (`verification.toml`, `.wiki/adapter.toml`, `.claude`,
`.codex`) is protected at any depth below every write root, so another
worktree's copy cannot be changed and committed either; no git setting
writes a file the guard has not placed (`GIT_TRACE*` is `top`), and gh's
rows change only pull request metadata or merge remotely.

Four rules hold across the table:

- A flag or parameter the row does not list is `top`, and so is a variable or
  substitution standing where an option may stand.
- A program with no row is only `spawn(name)`, which a grant naming it allows.
  A path-named program (`./helper.exe`) is that file, suffix included. A
  PowerShell cmdlet or member with no row is `top`: it runs inside the
  runspace, so no executable grant can stand for it.
- A name set by a builtin (`read`, `printf -v`, `mapfile`, `unset`) is judged
  like an assignment: setting an execution or destination variable (`PATH`,
  `HOME`, the proxy variables, `GIT_DIR`, `GIT_WORK_TREE`) that way is `top`.
- A directory change anywhere but the first statement (a later statement, a
  group, a branch, a pipeline, a substitution, a nested shell) is `top`, and
  so are loops, functions and traps, whose bodies run any number of times.
  PowerShell's `using module` and `using assembly` load code and are `top`;
  only `using namespace` is a declaration.

Everything else is denied in app-managed sessions: file writers such as `rm`,
`cp`, `mv`, `touch`, `Set-Content` and `Remove-Item`, archives, links, `awk`,
`sed`, `wget`, curl, and every git and gh form outside their rows. Follow-up
changes add rows for PowerShell and cmd, and shell semantics; a command that is
denied but ordinary is answered by adding a row there, not by loosening an
existing one.

| Effect | Granted when |
|---|---|
| `write`, `delete` | the path resolves (links, junctions and `..` followed) inside a write root: the checkout, or only the spec's `artifact_root` for a plan task; the temporary directories (`TEMP`, `TMP`, Python's); the session's `memory` folder; each `[guard] write_roots` entry, relative to the checkout |
| — never | `.git`, `.claude`, `.codex`, `.wiki/adapter.toml` and `verification.toml` of the checkout and its main clone, and any write or delete of a folder that holds them (`echo x > .wiki`, `echo x > .`). Only git's own write of the repository it runs in (`add`, `commit`, `merge`, `push`, …) is judged by place alone |
| `spawn` | the executable is one that `gate_cmd`, `verification.toml`'s round and local commands, a `[checks]` command or `python tool/search` runs, or is listed in `[guard] executables`. A bare name is that name; a name with a path (`./tool`, `C:/x/python.exe`, `/usr/bin/time`) is that file, granted only by the same path. A git remote name counts only in the checkout's own repository: a push after a `cd` into another repository is `top` |
| `network` | the host is the loopback, a configured git remote's host, or listed in `[guard] network_hosts`. A fetch, pull or push reaches every `url` and `pushurl` of its remote (every remote and URL upstream when none is named), and gh every remote's host; a remote at a local path, a URL rewrite (`insteadOf`) or `[include]` in any config file git reads (the repository's, global, XDG or system), a `[remote]` or `[branch]` section outside the repository's own config, `extensions.worktreeConfig`, or config syntax the guard does not decode (a key on a header's line, a continued, quoted or commented value), makes it `top`. `GH_HOST` and the proxy variables count as reaching their host (`GH_REPO` is `top`); a target whose host cannot be read is `top` |
| `top` | never |

The optional adapter table adds to the grant and never names a command:

```toml
[guard]
write_roots = ["../shared-fixtures"]
network_hosts = ["pypi.org"]
executables = ["ruff", "npx", ".venv/Scripts/python.exe"]
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
