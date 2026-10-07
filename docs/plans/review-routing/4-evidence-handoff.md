# Stage 4 — collect current evidence and hand it to the existing loop

See the [overview](0-overview.md). Prerequisite:
[code-owned composition](3-composition-provenance.md).

Purpose. Execute registered verification in its approved scope, validate actual
observations for the current task identity, and deliver the resulting contract
and receipts to the already authorized independent review loop. A validator
without a collector is not completion of this stage.

## Current state and implementation owners

`review_contract.ready()` and `verification.proven(..., flow_ids=...)` validate
existing API/browser receipts for ordinary tasks. `render()` supplies sanitized
observations to ordinary read-only reviewers. Contract/receipt changes invalidate
approval at completion, view and merge boundaries. Frozen preservation runs in
round/final checks through the existing refactoring owner.

Automatic runtime collection is currently wired through `loop.cloud_shipped()`.
`verification.execute()` can execute approved local commands, but failure,
instability and publication paths still assume Cloud ownership. Ordinary local
shipping does not call it automatically. There is no desktop flow kind, native
runner or native receipt validator. These are required work in this stage.

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
| 2 | Generalize execution | Shared selected-flow collection and owner-specific failure routing | Not started |
| 3 | Native collection | Versioned target contract, native runner and receipt validator | Not started |
| 4 | Verify end to end | Local/API/browser/native identity, lifecycle and handoff evidence | Not started |

## Migration and rollback

Enable general collection only for approved registered scopes; no automatic
production-target discovery. Preserve existing Cloud requirements/statuses and
legacy offline behavior. Disabling a collector leaves its enforced requirement
pending, never downgraded to offline-only. Keep failed attempts and artifacts;
do not delete source, user datasets or historical receipts to obtain a clean pass.
