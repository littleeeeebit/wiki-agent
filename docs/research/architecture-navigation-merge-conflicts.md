# Architecture navigation and merge conflicts

## What actually ran

The local error log records 45 `automatic-merge` failures for
`littleeeeebit/project-codeit-mid#14` from 2026-10-04 12:33:49 UTC through
13:19:49 UTC. Each reports that GitHub could not cleanly create the merge commit.
The exception came through `loop.automatic` and `_merge_spec`; the minute poller
retried the task while `auto_merge_pending` remained true. A read-only GitHub
query on 2026-10-05 finds that PR already merged. This change does not attempt
to merge it again or alter the project's current checkout.

`_merge_spec` checked the reviewed head, base branch name, passing final gate,
merge base and environment digest. It never tested whether the head could merge
with the current base tip. `allowed` had the same omission before publishing
`머지 가능`. A conflicting independent advance of `main` can leave both the
head and merge base unchanged, so all those guards can pass while GitHub refuses
the merge. This is reproduced with real Git repositories and a bare origin in
`tool/test_loop.py`; it does not depend on the error message alone.

The architecture screen previously inserted a static Mermaid SVG, showed raw
English descriptions and paths, and offered only a width-fit/natural-size toggle.
The saved documents already described diagram components at
`<parent path>/<node ID>`, including child diagrams. The screen did not use that
relationship. It also bypassed the existing Korean overlay.

## Sources and interpretation

The [Git merge-tree reference](https://git-scm.com/docs/git-merge-tree)
documents a merge simulation that does not change the index or working tree.
Its exit status is zero for a clean merge, one for conflicts, and another value
for failure. This implementation uses that status instead of searching conflict
markers or interpreting an empty file list as success.

The [Mermaid security configuration](https://mermaid.js.org/config/schema-docs/config-properties-securitylevel.html)
disables embedded click callbacks in strict mode. The app keeps strict rendering
and owns its navigation: rendered flowchart IDs are matched only to existing
direct child paths. No Mermaid callback, link directive or global function
provides navigation authority. The installed Mermaid renderer's generated node
IDs were inspected and checked in the browser.

## Changes and rejected alternatives

A merge preview fetches the exact base tip and simulates merging it with the
reviewed head before review, before and after the final gate, and immediately
before GitHub merge. A detected conflict sends one integration request through
the existing task implementation flow. The attempt is saved before dispatch;
the resulting commit must contain that exact base tip and receives new checks
and review. A restart cannot grant another repair against the same base tip.
External and cloud implementations retain their existing ownership. A planning
task stops for preparation because its replacement-artifact workflow does not
authorize Git conflict resolution. Nonconflict automatic merge failures also
clear automatic retry, retaining a visible fault for explicit continuation.

The guard does not change or stash working files. Conflict resolution belongs
to the implementation task and preserves both intended behaviors. Force-pushing,
resetting, choosing one entire side and reusing approval for a changed head are
rejected. No destructive Git operation is added to the merge endpoint.

Architecture boxes open their saved direct child document; leaf components show
their description. Breadcrumbs return to parent and overall views. The drawing
fits both viewport dimensions initially, accepts wheel zoom and right-button
dragging, and clamps zoom to at least the fitted size. Reset restores that view.
Buttons and keyboard controls provide alternatives to mouse gestures.
Human diagram labels are translated before layout so Korean text affects node
measurement; source-path lines and node IDs remain literal. Descriptions and
perspective titles use the same existing Korean overlay. Mermaid originals and
English documents retain their source text.

## Verification and limits

The Git regressions cover a conflicting base advance that leaves the merge base
unchanged, working-file/index preservation, conflict repair before review,
base movement after approval, new-head review, failed-repair persistence on
resume, external ownership and disabled automatic retries after policy failure.

The browser fixture checks Korean node and edge text, source-path preservation,
box clicks, keyboard leaf navigation, return to overall structure, wheel zoom,
right-button dragging, minimum-size guards and reset. It also renders the saved
architecture diagrams and checks desktop/mobile viewport containment. Model and
translation responses are fixtures; they do not establish real provider quality.

The scoped loop run executed 97 cases: 95 passed, one exposed a missing `dev`
branch in a mocked base-change fixture, and one exceeded its 15-second reviewer
startup wait during the concurrent Git-heavy runs. The fixture now creates its
real target branch; that case passes. The progress case passes in isolation and
uses the suite's standard 30-second startup allowance. Three additional timing
and preview-failure regressions pass. The planning and local-verification group
passes all 86 cases. The browser run, frontend build/lint, Ruff, wiki lint,
whitespace and UTF-8 without BOM checks pass. Existing frontend lint warnings
remain outside the changed components.

Executing the configured translation engine on four actual overall-architecture
labels returned Korean renderings for desktop/mobile clients, local application
server, scoped requests and task/review ownership. This checks real rendering
availability, not the translation quality of every description or provider speed.

Before review dispatch, fresh native Claude and Codex sessions both delivered
actual `SessionStart` hook injections of 6,238 context characters. The existing
`connect.probe` started each CLI and read its nonce-bound native hook receipt;
no hook was invoked manually. This confirms automatic session-start delivery,
not every host event or the quality of model answers.

Merge simulation detects Git conflicts, not every semantic incompatibility of
independent changes. GitHub can still reject policy, permission or checks, and
the base can move between the local preview and GitHub's merge operation.
A failed merge is checked again for that race and a discovered conflict returns
to integration and review. Neither this guard nor GitHub's head-match option is
an atomic lock on the base branch.
