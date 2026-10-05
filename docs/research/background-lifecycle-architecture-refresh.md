# Background lifecycle and architecture publication

Three independent reviews of PR #72 exposed ownership gaps beyond the original
missing-progress report. Round 1 examined `006c7e3`; round 2 examined `69461bb`.
Round 3 examined `1ce6c59`.
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

## Collected tasks and phantom follow-ups

The 2026-10-05 recurrence exposed an incorrect premise in that counter: a task
start does not promise a separate automatic response. The saved Langfuse work
transcript ended with a completed final report, then `waiting for 47 follow-up(s)`
and a 600-second silence timeout. Later correction turns retained five and one
phantom follow-ups. These are observed adapter records; they do not preserve
every original provider event or establish the origin of every historical result.

Anthropic's [SDK issue 1019](https://github.com/anthropics/claude-agent-sdk-python/issues/1019)
reports that a task collected through `TaskOutput` can terminate with only
`task_updated.patch.status=completed`, followed by the foreground result, without
`task_notification`. This is a provider issue report, not a guarantee covering
every CLI version. It explains why counting every start leaves an obligation
that the provider never created. The adapter already cleared the active task
on either terminal event; its separate follow-up counter remained inflated.

The first repair added a follow-up obligation only for an actual terminal
`task_notification`, once per task. It removed the collected-task counter leak,
but its assumption that every notification requires another answer was wrong.

Before repair, a persistent subprocess fixture with 47 collected tasks returned
no completed answer. After repair it completes two successive prompts in the
same provider process. A real-Git review regression uses the actual session
adapter with a simulated CLI: the first review refuses, the correction collects
47 tasks and commits, and the server checks, pushes and dispatches round 2 on
the new remote head without another button click. It reaches mergeable state.
The provider and GitHub are simulated; no user's task is resumed by these tests.

The running desktop app separately reproduced a correction ending at
`waiting for 4 follow-up(s)` while still using the old imported adapter. After
normal window shutdown and reopening with this repair, the saved Langfuse loop
resumed automatically and advanced from correction R2 to review R3. This is live
continuation evidence, separate from the simulated round-2 regression; it is
not a claim that the reviewed application's tracing findings are resolved.
The user then reported another stall. Before restarting, the live R3 correction
showed three completed command tasks, a pushed `ba7bebc`, a complete disposition,
and `waiting for 3 follow-up(s)`. The first repair was incomplete.

A direct protocol probe of the installed Claude CLI 2.1.289 established the
missing distinction without relying on that historical transcript. One
foreground `sleep 3; echo READY` command emitted, in order:

```text
task_started(task_type=local_bash, is_backgrounded=false)
task_notification(status=completed)
user(tool_result for the same tool_use_id)
assistant(text)
result(success, num_turns=2, terminal_reason=completed)
```

The CLI then waited for input; the adapter timed out instead of returning the
successful result. The installed binary's task-start schema describes
`is_backgrounded=false` as a foreground registration whose spawning tool blocks,
and `task_updated.patch.is_backgrounded` as a later move into the background.
This is observed installed-version evidence, not a promise for older versions.
The live R3 record is consistent with this sequence; its original raw provider
frames were not recorded, so that correspondence is an inference.

The adapter now retains that flag per task and applies later updates. Foreground
tasks remain visible but do not enter the background wait set or owe synthetic
replies on notification. Actual background tasks still drain their replies;
older streams without the flag retain the existing fallback. The counter is no
longer based solely on the notification's name.

The exact foreground sequence failed its regression before this change. It now
finishes three successive prompts in one persistent simulated CLI, and a
foreground-to-background transition still waits for its final synthetic answer.
The real-Git regression also runs three correction/review transitions to R4,
checking a new published head and a recorded disposition each time. All 49
session protocol tests pass, including steering, fast completion and batching.
An installed-CLI probe separately completes three foreground-command turns in
one process without phantom waiting. No review was dispatched by that probe.

After normal window shutdown and reopening with the foreground distinction,
the live Langfuse correction completed with an empty error field and recorded
its disposition in turn `74f2d958ab844668b0552854e2e802ff`. The app automatically
dispatched review R4 on the pushed `ba7bebc`, with no stopped reason. Its final
correction steps include the command's completion and hook response, without
a phantom follow-up wait. This establishes the repaired live boundary; R4's
review verdict and the other repository's tracing correctness are separate.

The same investigation found diagnostic gaps. Review stops and task-check
exceptions could be persisted or shown as ordinary progress without calling
the error recorder. They now write `review-stop`, `task-failure` and `task-check`
records; review stops include repository, task, PR, round, head and reason.
Unreadable/corrupt task records and failed queued dispatch also record their
exceptions. An absent task file is an ordinary lookup and creates no error.
Explicit user stops and server restart transitions remain ordinary lifecycle
events. Disk-open, write and rotation failures emit the already redacted JSON
record to stderr, and later calls can recover file logging. The default Python
handler's failure report is bypassed because it prints unredacted source lines.
One diagnostic `POST /api/errors` to the restarted server returned HTTP 200 and
wrote its matching `screen` record to the actual local JSONL file.

The Agent's no-selection view also hid its entire component, including controls
that the component already supported before task creation. The Agent now mounts
in that state, with the model picker and mobile options toggle available. Its
composer stays disabled until there is a task. Browser checks select a Codex
model at 1440px and 400px with empty task and checkout lists.
A separate browser session against the restarted live server selected
`GPT-6.1-Sol` in an empty Agent pane and confirmed that its composer was disabled.

## Other lifecycle and architecture evidence

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

Preserving a concurrently written note was insufficient: round 3's native
`omm write` reproduction preserved its Markdown but rolled back its newer
metadata counter and history. A note added to an obsolete element also retained
the directory while losing its parent registration. Publication now compares
document content, metadata and tree membership with the staging snapshot.
Any detected change aborts before publishing or pruning anything. It does not
merge native history heuristically or launch another paid scan automatically;
the recorded failure can be retried explicitly.

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

The first post-review full gate exposed two research-promotion regressions:
`knowledge.submitted()` passes a lightweight run without a `turn` attribute to
`specs.failed()`. Reading that attribute for diagnostics raised before the
original failure could be recorded in the task. Diagnostic turn identity is now
optional in both failure and caught-check recording. The existing promotion
regressions cover the missing-gate and failed-gate paths. A separate host-skill
test picked up real ancestor skills when its fixture was beneath the user's
directory; its relative repository root now keeps that scan inside the fixture.

`tool/test_agent.py` exercises early completion, explicit and absent origins,
separate and batched notifications, overlapping tasks, human steering, and two
consecutive prompts after a provider error using real subprocess transports.
`tool/test_architecture.py` verifies native metadata publication, obsolete
element removal, retained notes, binary assets, path boundaries and merge-only
dispatch with a deterministic CLI stand-in.
Concurrent metadata or note changes during validation must reject publication
and leave the live note, metadata, diagram and child registry intact.

The installed native OMM smoke independently advanced an update counter from
2 to 3, preserved the previous diagram, created new-child metadata and correct
Git provenance, kept an unchanged refresh byte-identical, and removed an
obsolete child from both the filesystem and native tree. This used no model.
An additional installed-CLI probe writes a note during staged validation, both
with and without attempted element removal. Both refreshes abort, preserving
the exact newer metadata bytes, history, note and parent registration.
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
The conflict check covers changes detected before publication; it is not a
cross-process filesystem transaction or a lock respected by external OMM CLIs.
