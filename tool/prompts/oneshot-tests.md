This turn writes the contract tests of a new backend, and nothing else.

The person approved the backend design. It is committed in this worktree:
`docs/design/backend.md` (the sheet they read) and `docs/design/api.yaml`
(the OpenAPI document). No backend code exists yet, and none may be written in
this turn.

The project's environment and stack: `{profile}`. Write the tests in the test
tool that stack uses, and set up what the tool needs to run them: the package
manifest, the environment, the tool's own configuration file.

## What to write

One test for each planned test below, named exactly its `test_name`, at the
top level of its file or class: not nested inside a suite or `describe` whose
name the report would put in front of it. Each test checks its `then` through
the endpoints it depends on, as `api.yaml` describes them. Reach the
application inside each test body, so the tests run while it does not exist
yet.

```json
{tests}
```

## What to return

End the turn with one fenced block:

```oneshot-runner
{"command": "…{report}…", "contract": ["…"], "config": ["…"], "root": ""}
```

- `command`: one line, run from the repository root, that runs every test and
  writes a JUnit XML report to `{report}`. Write `{report}` bare; the server
  puts a quoted path there. Most tools write JUnit with a flag or a reporter,
  some through a small wrapper (Go through `gotestsum`). Where the tool can,
  make each testcase carry its `file` attribute (pytest with
  `-o junit_family=xunit1`, jest-junit with `addFileAttribute`): without it a
  test in another file whose class and name read the same can stand in for
  a planned one.
- `root`: the folder, from the repository root, that the report's relative
  `file` paths start from. Leave it `""` when the tool runs at the root; write
  `web` when `command` runs it with `cd web && …` and it names files from
  there. The server reads every relative `file` from this folder.
- `contract`: every file that holds a planned test, or code a planned test
  runs from its own test code (a shared helper or base class).
- `config`: the files that only configure the test tool (`pytest.ini`,
  `vitest.config.ts` and the like). Keep the tool's configuration out of the
  package manifest, which the build will still change.

## What the server checks when this turn ends

1. Every file in `contract` and `config` exists in the worktree, and every
   file changed in this turn that names a planned test is in `contract`.
2. One run of `command` writes the report, and every planned test is in it
   and fails or errors: a planned test that passes now checks nothing, and a
   skipped one checks nothing either. No two of its cases share a class,
   name and file.

When both hold, the server writes `command` into `.wiki/adapter.toml` as the
gate, commits what this turn changed, and pins the `contract` and `config`
files and each planned case, by its class, name and file in the report. From then on
those files cannot change, and the build must make every pinned case pass in a
run of `command`. Make them right now.

## In this turn

- Write no `done-report` and no `test-cleanup` block. Committing is optional;
  the server commits what you leave.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the tests in this repository is fine.
