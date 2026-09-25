# Verification record for the public copy

This file is the record from the days of the public copy
(`ai-coding-agent-wiki-public`), kept as it was. The five channels and the
Slack runner below are gone from this repository. The current verification is
[`docs/plans/done/wiki-agent/7-verify.md`](plans/done/wiki-agent/7-verify.md).

## 2026-09-17 — functional changes applied

The project selection became shared across all five channels, and
conversations and records are now kept per project and per channel. External
briefings name a project too. The question rule is delivered on ordinary
utterances as well and distinguishes Claude Code's tool from Codex's. Hook
quoting for an adapter name containing a space was also fixed.

- 129 automatic checks, 10 separately executed checks, Ruff and the wiki lint
  all passed.
- The frontend lint and the TypeScript and Vite builds passed. The existing
  lint, bundle and test-client warnings remain.
- The core Python implementation, the frontend, and each rule's severity,
  triggers and block settings were compared against the original. The
  public-copy-only install guide, the fictional adapter, the path placeholders
  and the withheld-grounds handling were preserved.
- The original's real conversations, Markdown grounds records, project
  adapters, personal paths, generated graph and git history were not brought
  across. The question rule's private cases were excluded and
  `sources_withheld: true` kept.
- The public copy's tracked files and the whole existing history of three
  commits (178 distinct blobs) were compared against known private project
  names, personal paths and session identifiers, with zero detections.
- The real host question UI and an independent code review are not within the
  scope of these automatic checks.

This entry records verification of local changes. It does not mean a remote
push or a new public release.

## 2026-09-16 — the initial public copy

On 2026-09-16 this public copy was checked on Windows.

| Checked | Result |
| --- | --- |
| Automatic checks | 125 of 126 passed, then one install-order test was fixed; the 8 affected files re-ran and passed |
| Repository checks | Ruff, the wiki lint and 12 separately executed checks passed |
| The screen | npm ci, lint, and the TypeScript and Vite builds passed |
| A real install | Chat install in a fresh `.venv`, map generation, an existing Codex login surviving, and the model query all passed |
| Sign-in state | The current user's sign-in state was confirmed in the installed Claude Code and Codex CLIs |
| Hooks on both hosts | Install, reinstall and check passed in a separate local clone |
| Paths and settings | Paths with Korean characters and spaces, two same-named checkouts, reinstalling after a rename, and existing settings surviving all passed |
| The server | HTTP responses confirmed for the built HTML, the map, the project list, the model options and the channels |
| File format | UTF-8 without BOM and LF confirmed on every published file; map generation pinned to LF too |
| Distribution exclusions | An automatic check that conversation, analysis and private settings paths are excluded from git |

The server used for checking was stopped. No other server and no private
project's settings were changed. After fixing the map's line endings, three
related checks plus Ruff and the wiki lint were re-run and passed.

After restoring the rules and skills, the text and what was removed were
compared. The metadata of 21 rules is identical to the original apart from the
grounds paths, and the procedures of four skills are kept. Empty record
folders were left and the actual records excluded. `sources_withheld` was
confirmed to permit missing grounds only when exactly `true`, and to keep
catching link errors. The Slack launcher was checked with a fake CLI instead
of really sending, for passing private settings through and for stopping when
no channel is given. The chat installer is identical to the original. The
`graph.json` that is not published is generated separately, per the install
guide. The restored content was applied to a temporary clone and install,
reinstall and existing-settings survival were confirmed again on both hosts.
Two same-named checkouts, paths with Korean characters and spaces, and
reinstalling after a rename all passed.

Existing warnings remain: five frontend lint warnings, the large-bundle
warning and the test-client dependency warning. None were hidden behind a
failing check, and this separation for distribution changed none of the
related features.

## The publication-scope check

The original's `.git`, real user utterances, per-project adapters, private
settings and generated records were not copied. The 21 rules and four skills
exclude only the real grounds records from the text. Applicability, severity,
block settings, and each skill's steps and exit conditions keep the original.
Tests that depended on real records were switched to fictional input, and real
incident statistics were not used as evidence for functional verification. In
the first commit's 128 files and their history, no known existing project
name, personal path, session identifier, original commit reference or common
credential pattern was detected. The original's 85 session identifiers were
compared too. Further changes and commits have to go through the same check
again.

This is a local pattern check and a content review. It is not a certification
guaranteeing the absence of every form of secret. No photographs or
screenshots are included, and the one PNG present was confirmed to be
decorative shapes.

## What needs confirming separately

- Signing in with a new account through real OAuth was not performed. An
  existing login surviving, and its state, were confirmed. The checks that use
  different CLI settings paths ran with fake CLI processes.
- Whether a real Claude or Codex session delivers automatic events, and the
  options question UI, are separate verification targets.
- The plain explanation's quality does not pass yet. It follows the
  [review criteria](quality.md).
- Real installs on macOS and Linux are not included in this Windows result.
- With no independent review session to reuse, this result is the
  implementer's own checking. No claim is made of an independent code review.
- Creating a GitHub remote and pushing were not performed.
