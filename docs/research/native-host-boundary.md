# Native host boundary and task PR recovery

The app's implementation agent selected a legacy desktop skill and attempted
external terminal discovery during ordinary task work. Separately, the review
button rejected a manually published task PR because its specification already
existed without a PR association. Both failures prevented the app's own review
workflow from progressing.

## Observed causes

Saved work events showed selection of `orca-cli` and `review-loop`, followed by
executable discovery, terminal inspection and terminal-send calls. This was an
executed path, not an inference from a source-code search. The hub's always
available delegation page also named external terminal discovery, and its review
skill supplied the terminal-send command. App-owned sessions inherited the
launching host's skills and transport environment.

The runtime still carried a second transport: the search daemon's `Keeper`
called the external CLI to read and type into idle Claude terminals. Installers
and session discovery also scanned another application's account layout. These
were separate dependencies, even when they did not cause the reported task failure.

The affected task record was in `작업 중` with its original requirements and
branch, but `pr` was null. `loop.take` matched only PR numbers; it missed the
existing task and fell through to new-spec creation. The branch slug then hit
the duplicate-name check. The saved conversation separately recorded an
automatic approval rejection of a direct metadata edit. That rejection is not
a server state-transition rule; supported PR adoption was missing.

## Changes and ownership

Native SessionStart, UserPromptSubmit, PreToolUse and Stop hooks keep context,
rules, command checks and reconciliation. `host_boundary.py` redirects accidental
external-host commands with native workflow context and fails open on exceptions.
The shared rules and review procedure now route review through the app.

App-owned sessions drop inherited transport variables while retaining the
selected CLI login. Claude disables automatic skills for managed sessions;
Codex uses process-local overrides for inherited skills referencing the retired
host. The host's user skill files remain intact. Connection probes use the same
boundary. Hook reinstall retires the identified external hook bridge and this
hub's keepalive commands while preserving unrelated hooks. Search has no terminal
transport, and discovery reads only the default or explicitly selected Codex home.

For a manually published task PR, the server matches the exact task branch and
checks repository, checkout and branch identity. It refuses busy or mismatched
checkouts before changing metadata. Under the specification lock it attaches
the validated PR, preserves requirements and revision history, invalidates old
gate evidence, and dispatches the existing review loop. No duplicate task or
replacement worktree is created.

## Verification

- The route regression recreates PR #11 with an existing unlinked task, for both
  a linked worktree and a shared checkout. The button reaches an independent
  review result, preserves task identity and supports a later review request.
  Busy and wrong-branch cases leave metadata untouched. Git and the HTTP routes
  are real; GitHub and model replies are controlled stand-ins.
- Hook subprocess checks cover redirection, malformed input, UTF-8 output and
  unrelated native commands. Reinstall checks preserve foreign hooks, remove
  the old bridge and retired owned hooks, and remain idempotent.
- A read-only request to the installed Codex app-server confirmed that
  `orca-cli`, `orchestration` and the inherited desktop `computer-use` skill
  were disabled; the rewritten native `review-loop` remained enabled.
- Fresh Claude and Codex sessions both delivered actual SessionStart events:
  each probe recorded 5,625 context characters. These are host-event checks,
  distinct from installer wiring checks and direct hook invocation.

The full `python -m pytest -q tool --tb=short` run recorded 1,354 passes,
one skip and one failure in an outdated fake app-server instruction assertion.
The fixture was updated to require the original read-only behavior plus the
new native runtime boundary; its entire module then passed all 61 tests.
After the last transport-guard changes, the 50-test hook/install group and
19-test native boundary group passed. Python lint, hub/repository lint and
whitespace checks were clean. The skipped case is not counted as verified.

The command hook prevents known accidental transport paths. It is not a sandbox
for arbitrary encoded or dynamically generated shell programs. Historical plans
remain evidence of earlier implementations, not current runtime instructions.
Existing running app processes must restart to load changed Python code.

Codex's per-skill controls and additive hook discovery are described in the
[official skill configuration](https://learn.chatgpt.com/docs/build-skills)
and [hook documentation](https://learn.chatgpt.com/docs/hooks). Adding a higher
precedence hook does not remove a lower-layer desktop bridge, which is why the
installer explicitly retires that bridge.
