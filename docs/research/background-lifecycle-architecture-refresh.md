# Background lifecycle and architecture publication

Two independent reviews of PR #72 exposed ownership gaps beyond the original
missing-progress report. Round 1 examined `006c7e3`; round 2 examined `69461bb`.
Their results and native receipts are under `artifacts/review/` locally. The
reproductions below also have committed regression tests, so the reasoning does
not depend on retaining terminal output.

## Observations and rejected assumptions

The adapter discarded Claude background lifecycle and raw tool-result events,
and stopped at the first foreground result. Forwarding those events fixed
visibility, but checking only the currently running task set did not establish
completion: a fast task can finish before the foreground result while its
automatic response is still owed.

Counting follow-ups repaired that race, but an unattributed response from task A
must settle A even while task B still executes. Requiring the entire running set
to be empty conflated independent obligations. Replayed human and synthetic
messages provide a boundary on older streams; a steered human response must not
discharge a background obligation.

An error result is terminal for the caller, not proof that its reusable provider
process has no pending events. A two-prompt subprocess reproduction returned an
old task's response for the second prompt. Failed `done` events must therefore
close the failed session before another prompt can reuse its event channel.
Normal successful sessions remain reusable; conversation identity survives
reconnection.

Architecture publication has the same distinction between visible content and
owned state. Staging individual native `omm write` calls but discarding their
`meta.yaml` output lost history, counters and child registration. Copying and
publishing that metadata repaired additions, but removing only the old generated
description and diagram still left metadata-only elements visible in `omm tree`.
Cleanup must preserve maintainer content, remove otherwise empty owned elements,
and reconcile the native parent registry with the staged directories.

## Sources and interpretation

The [Claude Python ResultMessage reference](https://code.claude.com/docs/en/agent-sdk/python#resultmessage)
defines result `origin` as the provenance of the triggering user message. The
[TypeScript reference](https://code.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage)
specifies task-notification results and explains that batched completions can
produce empty zero-turn results before the final combined response. These are
protocol facts. Separately tracking execution and owed results is this
repository's interpretation for a single app-owned work turn.

The installed `oh-my-mermaid` implementation's `writeNodeField`, `updateMeta` and
`deleteClass` were inspected before the corresponding repairs. Native writes
produce metadata and history; native deletion removes the directory but does
not update its parent's children. Reconciliation uses the already-required
PyYAML dependency rather than introducing a second metadata format or scanner.
The CLI remains responsible for changed architecture fields and native write
history; the application owns staged validation, publication and ownership
cleanup. A removed element with notes or other maintainer content is retained.

## Verification and limits

`tool/test_agent.py` exercises early completion, explicit and absent origins,
separate and batched notifications, overlapping tasks, human steering, and two
consecutive prompts after a provider error using real subprocess transports.
`tool/test_architecture.py` verifies native metadata publication, obsolete
element removal, retained notes, binary assets, path boundaries and merge-only
dispatch with a deterministic CLI stand-in.

The installed native OMM smoke independently advanced an update counter from
2 to 3, preserved the previous diagram, created new-child metadata and correct
Git provenance, kept an unchanged refresh byte-identical, and removed an
obsolete child from both the filesystem and native tree. This used no model.
Fresh Claude and Codex sessions delivered actual SessionStart and
UserPromptSubmit hooks after the adapter repairs. The earlier live Claude
background execution and desktop/mobile display runs remain separate evidence.

Protocol fixtures are not proof of every provider ordering. Older streams that
omit both result origins and replay provenance require the documented
foreground-then-follow-up fallback; their provenance cannot be reconstructed
perfectly. Windows symbolic-link verification remains skipped on hosts without
link privileges. No review restarted the user's running app or changed their
installed host configuration. Full-gate evidence is recorded only after an
independent approval of the exact head.
