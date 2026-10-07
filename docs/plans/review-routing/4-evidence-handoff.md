# Stage 4 — collect current evidence and hand it to the existing loop

See the [overview](0-overview.md). Prerequisite:
[code-owned composition](3-composition-provenance.md).

Purpose. Execute registered verification in its approved scope, validate actual
observations for the current task identity, and deliver the resulting contract
and receipts to the already authorized independent review loop. A validator
without a collector is not completion of this stage.

## Current state and implementation owners

`review_contract.ready()` and `verification.proven(..., flow_ids=...)` validate
registered API/browser/command/native receipts for ordinary tasks. `render()` supplies sanitized
observations to ordinary read-only reviewers. Contract/receipt changes invalidate
approval at completion, view and merge boundaries. Frozen preservation runs in
round/final checks through the existing refactoring owner.

Automatic runtime collection runs through `loop.cloud_shipped()` for Cloud and
the enforced contract preparation in `loop.step()` for local/external tasks.
`verification.execute(..., flow_ids=...)` uses approved selected flows, persists
attempts, rechecks source/spec/environment identity and routes failures by owner.
Version-2 manifests/settings support the Windows Win32 collector in
`tool/native_verification.py`. It launches only owned disposable targets on an
isolated desktop and validates measured builds, controls/events and artifacts.
The [local verification guide](../../local-verification.md) records its setup and
supported scope. Wider installed-host/hook acceptance remains separate.

Reuse `main/verification.py`, `main/loop.py`, registered project scripts, managed
process/cancellation utilities and current receipt persistence. Do not duplicate
the review engine or give ordinary reviewers Cloud execution tools.

## General local live collection blueprint

Split execution from implementation-owner recovery in the existing verification
module. Use a small owner discriminator, not a plugin/factory framework. The
shared runner prepares the environment, executes missing selected flow IDs,
validates receipts, persists attempts and cleans up. Its result identifies
preparation failure, functional failure, instability, interruption or success;
the loop routes that result to the existing implementation owner.

| Implementation owner | Required recovery |
| --- | --- |
| Ordinary local task | Existing local work session receives concrete observed failures under its current authorized workflow |
| External implementation | Existing external correction/wait path; do not launch a replacement local implementation |
| Claude Code Cloud | Preserve `return_to_cloud` handoff, failure records and current GitHub status/protection rules |
| Plan-only artifact | Review acceptance design; do not launch the future application's runtime |

An authorized review loop may collect its mandatory registered proof. Creating a
plan/spec or recording a Jev candidate alone does not start review or collection.
Local setup is a runtime prerequisite, not justification for leaving the general
collector machinery unimplemented.

Add selected `flow_ids` to the execution interface, or an equivalent validated
required-flow subset. Cloud keeps its full mandatory list. Only enforced stage 3
items are executable; shadow candidates are not. Validate IDs and exact approved
manifest digest before any setup/command runs. Include required offline and
frozen-preservation checks rather than replacing them with live evidence.

### Execution sequence

1. Snapshot spec/head/base, enforced contract, clean checkout, approved manifest
   and local settings. Refuse stale identity or unknown commands before execution.
2. Check approved origins, test account/dataset scope, required environment
   revisions, env-file handling and registered setup/cleanup. Preserve an unrelated
   existing `.env`; secrets remain local and excluded from Git.
3. Create a task/attempt-owned artifact location outside tracked source. Persist
   an unfinished attempt before launching setup or a flow.
4. Reuse only receipt identities permitted by impact/signature checks. Otherwise
   run selected registered commands using the managed foreground process path.
5. Parse actual observations, compare assertion IDs/expected results and validate
   execution/build/environment identity. Exit zero alone is insufficient.
6. Recheck environment and source identity before accepting observations. Persist
   failures before fallible environment rereads so a setup error cannot erase them.
7. Clean up only resources and fixture records created by this attempt. Cancellation
   stops owned execution and still runs bounded cleanup; cleanup failure holds readiness.
8. Route the typed outcome to its owner. Do not auto-retry identical failures until
   they pass or relabel same-identity fail-then-pass as stable evidence.
9. Revalidate and render current evidence before dispatching the existing reviewer.

Keep existing attempt history and instability/research holds. General recurrence
research policy remains owned by the [recovery plan](../reliability/8-recovery.md);
this refactor must neither claim to complete it nor bypass existing Cloud holds.
Restart never treats an unfinished attempt as successful. It resumes valid
completed rows and explicitly retries/repairs interrupted ownership under existing
authorization; no orphaned worker or duplicate setup is inferred from stale state.

## Evidence acceptance contract

Retain the current assertion-based API/browser format described in
[local verification](../../local-verification.md). Extend versioned records only
where native identity or current-spec binding requires new fields.

| Evidence | Required observations and binding |
| --- | --- |
| Offline/command | Exact registered check/assertions, exit/result, reviewed head and command/environment identity |
| API | Actual method, allowed-origin URL, response status and expected/actual/pass observations; approved target and test scope |
| Browser | Actual actions and visible results, measured build head/browser tool, plus required actual API requests under the current schema |
| Differential | Frozen-input digest, protected-test baseline, current-head rerun and unchanged protected inputs |
| Native desktop | Actual app/window/host actions and observations, executable/build and host identity, supported native collector |
| Performance attention | Representative workload and before/after measurements through registered assertions; green correctness checks alone are not performance proof |

Bind readiness/approval to spec signature, enforced digest, head/base,
manifest/flow-command digest, environment signature and accepted receipt identity.
Receipt reuse across heads must retain the original executed head and the explicit
unaffected-impact reason; shared/unmapped/unknown changes or environment revisions
invalidate reuse. A changed acceptance assertion requires new review/proof even
if an old command still exits successfully.

Known environment revisions are declared by the setup owner; the current code
cannot independently observe every remote deployment or dataset replacement.
If acceptance depends on the real remote version, register a flow observation
that measures it. Do not describe a user-entered revision label as attestation.
Registered scripts/receipts are trusted project verification inputs, not
tamper-proof independent measurements; the reviewer still examines their adequacy.

## Native desktop collector blueprint

Support one explicitly named native runtime first, with Windows as this project's
initial implementation target. Unsupported OS/host combinations stay preparation
failures. Inspect available runtime capabilities before choosing a driver; reuse
an existing approved native control path if available. A browser driven at desktop
viewport size is still browser evidence, including
`web/tests/desktop_browser.py`.

Respect the [native-host boundary](../../research/native-host-boundary.md):
app-managed wiki-agent must not rediscover Orca terminals or another host's account
transport. This session running in Orca does not make Orca a production dependency.
The collector is an approved native test runner, not a new agent/reviewer session.

Extend the manifest and local settings with a versioned `desktop` kind and closed
native target settings. Older version-1 API/browser/command manifests remain
valid; code lacking the new version must fail clearly rather than accept unknown
fields. Native scopes and runner commands require explicit setup approval.

| Native field/group | Validation requirement |
| --- | --- |
| Target | Registered application/host, approved executable/path, owned launch or explicitly approved attachment |
| Build | Measured executable/build identity mapped to reviewed head, not merely echoed expected head |
| Window | Observed process/window identity; verify target and focus immediately before each input |
| Environment | OS/runtime/driver versions, display scale/geometry and test profile/account/data revisions |
| Actions | Registered action/selector and expected/observed result; no arbitrary model-generated input sequence |
| Assertions | Complete expected/actual/pass results; real events when acceptance requires lifecycle/hooks |
| Artifacts | Scoped event logs/screenshots with hashes/locators, privacy filtering and ownership |

A single screenshot or an inferred DOM result cannot prove a native hook event,
window behavior or executable identity. Prefer native accessibility/event evidence
where available; screenshot-only assertions must declare their limited visual
coverage. Missing selectors, changed target/focus, stale build or absent required
event stops the flow, not a click into the currently focused unrelated window.

Use a disposable app/profile/test dataset for mutation scenarios. Permit only
registered actions in the approved test scope. No broad desktop keyboard input,
terminal commands, elevation, account discovery or real-user data deletion.
Never close an unrelated application during cleanup. Persist acquired process,
window and fixture ownership for cancellation/restart cleanup. If attachment
cannot establish safe ownership, return an explicit preparation reason.

Implement one runner and its receipt validator before adding host-generalization
layers. Driver-specific identity belongs in the native receipt, while review
selection and loop handoff remain shared. Actual native acceptance must exercise
the supported host; fake window tests validate protocol but do not complete it.

## Readiness, review and final gate

Reuse existing loop states; use verification outcome reasons instead of a second
state machine. Missing configuration, missing evidence, failed behavior, unsupported
host, stale evidence and cancellation remain distinguishable. No preparation
problem can be converted to reviewer approval by Jev or an offline pass.

Readiness is a server check. Pass the effective rubrics, enforced criteria,
selection grounds, acceptance assertions and sanitized receipts into the existing
round instruction. Keep ordinary reviewer tools read-only. Preserve the existing
Cloud reviewer contract until an independently verified migration changes it;
this plan does not silently remove its current execution capability.

Recheck identity after review and at final gate/merge eligibility. Retain findings,
round protocol, dispositions and stale-result handling. Run focused checks during
repairs; the final full gate remains on the reviewed head. Review readiness is
not review authorization; review approval is not permission to push/merge/deploy.

## Verification matrix and completion evidence

| Case | Required observation | Evidence class |
| --- | --- | --- |
| E01 Offline-only local task | Existing targeted checks/review/final gate; no runtime runner started | Automated protocol |
| E02 Local API acceptance | Registered isolated running API collected automatically; correct receipt reaches read-only reviewer | Isolated live API plus protocol |
| E03 Browser save/reload | Actual browser actions and persisted API result bound to tested build | Live browser/API |
| E04 Local/external/Cloud failure | Each returns to its own implementation owner; no cross-owner repair | Protocol; actual owner path where supported |
| E05 Missing/changed setup | Clear preparation reason; no command starts on an unapproved digest | Automated negative |
| E06 Stale head/spec/base/environment | Old proof/approval rejected at readiness, completion and merge eligibility | Automated negative |
| E07 Same-identity fail then pass | History retained and instability hold, not silent success | Automated fault injection |
| E08 Cancel/restart/cleanup failure | Owned work stops; unfinished proof not passed; unrelated resources survive | Fault injection plus owned live resource check |
| E09 Native target flow | Real supported app/window/build and required event assertions collected | Actual native host |
| E10 Wrong focus/build/host | No input into unrelated target; browser receipt cannot satisfy native obligation | Native negative plus protocol |
| E11 Shadow non-interference | No collection caused solely by a candidate recommendation | Automated protocol |
| E12 Preservation plus runtime | Both frozen checks and actual registered runtime proof required | Refactor/API integration |

Extend `tool/test_local_verification.py`, `tool/test_review_contract.py` and
relevant existing loop tests with temporary repositories and controlled failure
fixtures. Add the smallest dedicated native check required by the chosen runner.
Record exact commands, commit/working-tree identity, config hashes, expected and
observed results, artifact locators and limitations. Do not copy a proposed case
into a report as passed before running it.

This stage completes only when E02 automatic local collection and E09 actual
native collection are demonstrated, owner/identity/safety regressions pass and
the final handoff remains the existing loop. A held setup or unavailable supported
host keeps the corresponding implementation/acceptance status open. The broader
reliability desktop matrix retains its own additional requirements.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Existing validation | Registered receipt checks and sanitized ordinary-loop handoff | Done — current focused suite |
| 2 | Generalize execution | Shared selected-flow collection and owner-specific failure routing | Done — automatic isolated API/browser collection, selected-flow approval and local/external/Cloud ownership implemented |
| 3 | Native collection | Versioned target contract, native runner and receipt validator | Done — Windows Win32 owned desktop/target, measured build/control/event receipts and configuration approval implemented; actual native fixture exercised |
| 4 | Verify end to end | Local/API/browser/native identity, lifecycle and handoff evidence | In progress — actual API/browser/native, ownership, identity and preservation checks passed; full suite 1,743 passed and 4 skipped; final repository gate held by five pre-existing ratchet violations |

## Migration and rollback

Enable general collection only for approved registered scopes; no automatic
production-target discovery. Preserve existing Cloud requirements/statuses and
legacy offline behavior. Disabling a collector leaves its enforced requirement
pending, never downgraded to offline-only. Keep failed attempts and artifacts;
do not delete source, user datasets or historical receipts to obtain a clean pass.

## Working-tree acceptance record — 2026-10-08

The implementation is an uncommitted working tree on `main`, based on
`a411589bcc8d905f818474369976c1284d590e26`. This records local verification;
it is not an independent review, release or merge approval.
The 12 changed/new Python and TypeScript source files have combined SHA-256
`e3c57ec5547157ad0f3145e23b81835dc664a2049a9ee41d42beb2ece1f7f4da`:
sort relative paths, then hash each UTF-8 path, NUL, file bytes and NUL.

| Command | Observed result |
| --- | --- |
| `python -m pytest -q tool --tb=short` | 1,743 passed, 4 skipped, 1 existing Starlette deprecation warning; 2,336.05 seconds |
| `python -m pytest -q tool/test_native_verification.py` | 2 passed on Windows, including actual owned-window/event collection and native negatives |
| `python -m pytest -q tool/test_local_verification.py::test_automatic_browser_collects_actual_save_reload_api_observations` | 1 passed with actual Chromium, HTTP POST 201 and GET 200 observations |
| `python -m pytest -q tool/test_native_verification.py tool/test_local_verification.py::test_changed_managed_env_is_rejected_before_any_flow_command tool/test_local_verification.py::test_identical_user_env_is_not_claimed_or_overwritten_on_later_source_change --tb=short` | 4 passed on final sources, 25.74 seconds |
| `python -m ruff check tool` | Passed |
| `python tool/lint.py --check` | Passed, 26 pages checked |
| `npm --prefix web run lint` | Passed with existing warnings |
| `npm --prefix web run build` | Passed; existing bundle-size warnings remain |
| `git diff --check` | Passed |
| `python tool/debt.py check` | Held by five pre-existing ratchet violations listed below |

An earlier full run returned 1,737 passed, 4 skipped and 1 failed while native
receipt code was being edited: the already-imported validator and newly started
runner used different receipt field versions. Fresh native runs passed after
the edit; the final full run above supersedes that mixed-version run.
One user-owned `.env` regression was added after the full run collected its
tests. The final four-test command covers that addition and rechecks native
and environment behavior on the final sources; current collection is 1,748
tests, compared with 1,747 collected by the full run.

| Cases | Executed evidence and limits |
| --- | --- |
| E01, E11 | Existing offline/shadow regression checks retain preparation holds, read-only tools and non-authoritative candidates; candidate-only recommendations do not start collection |
| E02, E04 | Real loopback HTTP API exercised through automatic local/external collection, read-only reviewer handoff and owner-specific failure paths; existing Cloud ownership regressions retained |
| E03 | Actual browser save/reload against a disposable server; the server's in-memory value survives page reload, with measured Git build identity |
| E05, E06 | Unknown/duplicate flows, stale approval digest, environment drift, spec/source/base changes and altered evidence hold readiness; a pre-existing identical user `.env` remains unowned and is not overwritten after source changes |
| E07 | Injected fail/pass on the same identity retains both attempts and requires recorded investigation before ordinary-loop resume; existing Cloud investigation regressions retained |
| E08 | Cancellation reaps an owned live process tree while an unrelated process survives; cleanup failure retains observations without readiness; restart keeps completed observations and does not silently replay |
| E09 | Actual Windows Win32 fixture launched on an attempt-owned desktop, measured executable/Git input, focused control, `Saved` observation and `WM_COMMAND/save` event; owned launch cleaned before handoff |
| E10 | Actual missing control and wrong focus stop without a save event; modified build/host/focus/event receipts and artifact tampering are rejected by validation/readiness/merge checks |
| E12 | Parameterized preservation integration retains frozen checks at round/final gates; its live variant additionally requires automatically collected API evidence |

Git, HTTP, Chromium and Win32 interactions above are real. GitHub, reviewer and
implementation-worker transports are test stand-ins. This does not demonstrate
production account permissions, installed wiki-agent hooks/lifecycle, physical
keyboard/compositor behavior or the broader reliability desktop matrix. Existing
performance obligations remain enforced preparation holds without a registered
collector; they are not treated as offline success.

The successful native observation is snapshotted before deliberate negative
artifact corruption at the following local locator:

```text
%TEMP%/wiki-agent-verification/2569a2088195b905c13002cfd0519555bb108af0e0bc4a9f1743493fb55c9f1a/1474d914c70a4f5f89b6a5ab256943ef/native-save/acceptance.json
```

That snapshot records test head
`3a716c6c1c65b27dd05c77bccbf8e67f43e3ae1c`, expected/actual `Saved`, a passing
assertion, PID `12060`, HWND `21893292` and the observed `WM_COMMAND/save` event.
Its approved configuration digests are:

| Input | SHA-256 |
| --- | --- |
| Manifest | `31f50535bbba31d95851d736c549f8da1bfc900f3d9e96631fd1979eb0aad3d6` |
| Native settings | `0ee68b92a137b6dffef6a57e69578c6a1a7fe7f2e5d0a5854bc9428818c3c32b` |
| Native target | `62afa66a076abbb37dadf0261929dcc54f358ee61c13fd4548fc311e8305729d` |

The negative test intentionally modifies `observations.json` afterward and
confirms readiness and merge eligibility are revoked. The snapshot documents
the earlier successful observation; that tampered attempt is not current proof.

The final repository gate is still held by baseline violations in
`tool/agent/chat_session.py` (1,360 lines; ceiling 1,291), `tool/main/work.py`
(1,066; 1,060), `tool/test_agent.py` (1,221; 1,010), `tool/test_loop.py`
(2,374; 2,328) and `web/tests/desktop_browser.py` (6 duplicate lines; ceiling 0).
These files and their ceilings were not changed by this stage. Stage completion
remains open until that gate passes on the reviewed revision.
