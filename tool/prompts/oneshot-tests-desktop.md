This turn writes the contract tests of a new Windows program's core, and the
project skeleton they need, and nothing else.

The person approved the core design. It is committed in this worktree:
`docs/design/core.md` (the sheet they read) and `docs/design/core.json` (the
modules and the outside calls). No core code exists yet, and none may be
written in this turn.

The project's environment and stack: `{profile}`. Set up the project in that
stack: the build files, the program's project, and the test runner's
configuration, so that the program can be built once the core exists.

{tools}
## What to write

One test for each planned test below, named exactly its `test_name`, at the
top level of its file or class: not nested inside a group or `describe` whose
name the report would put in front of it. Each test checks its `then` against
the contracts of the modules it depends on, as `core.json` describes them.
The tests run on this PC in the stack's own host-side runner, with no window
of their own and no Windows session: `cargo test` for a Rust core,
`dotnet test` for .NET, `vitest` or `jest` for TypeScript. Reach the module
inside each test body, so the tests run while it does not exist yet. A call
that leaves the computer, and every Windows API a platform module wraps, is
replaced by a fake in the tests; none goes to the network or the screen.

```json
{tests}
```

## What to return

End the turn with one fenced block:

```oneshot-runner
{"command": "…{report}…", "contract": ["…"], "config": ["…"], "root": ""}
```

- `command`: run from the repository root, it runs every test and writes one
  JUnit XML report file to `{report}`. It is plain commands joined only by
  ` && `: no `;`, `|`, `#`, backtick, `$(`, brackets, braces, lone `&` or new
  line, and no command that starts with a shell's own word such as `exit`,
  `exec`, `eval` or `iex`. Each command starts with a declared tool or a file
  in this worktree, as above. The published gate appends more after it. Write
  `{report}` bare; the server puts a quoted path there. Where the tool can,
  make each testcase carry its `file` attribute: without it a test in another
  file whose class and name read the same can stand in for a planned one.
- `root`: the folder, from the repository root, that the report's relative
  `file` paths start from. Leave it `""` when they start at the root.
- `contract`: every file that holds a planned test, or code a planned test
  runs from its own test code (a shared helper, a fake, a base class).
- `config`: the files that only configure the test runner. Keep the runner's
  configuration out of the program's build file where the tool allows it,
  since the build will still change that file.

## What the server checks when this turn ends

1. Every program `command` starts is a declared tool or a file in the
   worktree.
2. Every file in `contract` and `config` exists in the worktree, and every
   file changed in this turn that names a planned test is in `contract`.
3. One run of `command` writes the report, and every planned test is in it
   and fails or errors: a planned test that passes now checks nothing, and a
   skipped one checks nothing either. No two of its cases share a class,
   name and file.

When they hold, the server writes `command` into `.wiki/adapter.toml` as the
gate, commits what this turn changed, and pins the `contract` and `config`
files and each planned case, by its class, name and file in the report. From
then on those files cannot change, and the build must make every pinned case
pass and the whole gate succeed. Make them right now.

## In this turn

- Write no `done-report` and no `test-cleanup` block. Committing is optional;
  the server commits what you leave.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the tests in this repository is fine. Do not start the program or
  open a window.
