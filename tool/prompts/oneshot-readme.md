This turn writes the README of a finished project, and nothing else of substance.

The person approved the backend, the screens and the finished frontend. The
code is committed in this worktree; the design is in `docs/design/`. What is
left before the first pull request is the page a newcomer reads first.

## What to write

`README.md` at the repository root, for {os}:

- What the application is for, in two or three sentences from the design.
- Setup: the exact commands that create the environment and install what
  the code needs, as this repository does it, for each operating system named
  above where the commands differ.
- Run: the command that starts the application, and where it is then served.
- Test: the gate command, `{gate}`, and what it covers.

Every command must be one that works in this repository as committed. Write
the README in the language the design documents are written in.

{workflow}

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done`, each passing.
2. `README.md` was written, and a CI workflow exists exactly where it is
   asked for above.
3. The contract tests and the test tool's configuration are unchanged.
4. The whole gate `{gate}` passes on a clean, committed HEAD.

Then the server asks you for the pull request's text, and opens it itself.

## In this turn

- Change no application code and no test. Commit everything and leave
  nothing uncommitted, then end with the `done-report`.
- Do not push, open a pull request, or ask the person anything.
