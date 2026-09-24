# Managing the private original and the public copy

The private original stays in use, with its records and history intact. The
public copy is a separate git repository that keeps the original's structure,
rules and skills and excludes only the private records. Do not import the
original's git history, and do not merge an original branch into the public
copy.

## The update order

1. From the files changed in the original, pick the changes the public copy
   needs.
2. Do not auto-sync whole files; review the change and patch it into the
   public copy.
3. Confirm that comments, tests, prompts and examples carry no private project
   information either.
4. Remove only real conversation quotes, internal code, per-project paths and
   session links. Do not summarise or rewrite a rule. Compare each rule's
   applicability, severity and block settings, and each skill's steps and exit
   conditions, against the original.
5. Run the functional checks and confirm what will actually be committed with
   `git diff --cached`.
6. Check for more than credentials: plans, internal code, conversations,
   repository addresses and personal paths.
7. After committing, inspect the whole history of the branch to be published,
   and push that public copy alone.

`raw/`, `.wiki/`, `.claude/`, `.codex/`, `.chat-local.json`, `artifacts/`,
`graph.json` and a private adapter are excluded from git by default. Do not
force-add credentials, settings or experience records. `.gitignore` removes
neither an already-committed file nor past history.

## Keeping the existing experience

The private wiki keeps working from its existing path. Installing the public
copy does not replace the private wiki or a project's settings. Which wiki a
project connects to is chosen when installing in that project.

The public copy's install checks run against the code inside the public copy
and fictional examples. Real project records are not copied into the public
copy for the convenience of a check. Access and licensing of the public
repository are decided by its owner.

## Rules with their grounds withheld

An existing rule's `sources` is emptied and `sources_withheld: true` marks the
original grounds as private. That mark changes neither the rule's severity nor
its applicability, and it does not mean the original experience was publicly
verified. `lint` allows the missing grounds only for rules carrying it; every
other check runs unchanged. Do not apply it to a new rule that simply has no
grounds.

## Private values and optional features

A project adapter is configured fresh in each checkout. The empty structure of
`raw/` and `.wiki/` stays, but the utterances, decisions and measurement files
accumulated before are not distributed. Records that accumulate after
installing are excluded from git too.
