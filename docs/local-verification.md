# Local verification of cloud implementations

Claude Code Cloud implements from the repository. A separate local reviewer
checks the resulting commit with this machine's test API, dataset and browser
configuration. Cloud does not receive `.env`, credentials or private datasets.
The existing `plan`, `code` and `mixed` profiles still determine review criteria.
Locally implemented tasks keep their existing review and final gate. An authorized
review loop also collects enforced registered flows for local and external tasks.
Their reviewers stay read-only. Failure returns to the existing local work session
or external correction path; shadow advice never selects executable flows.

The Cloud review cell also executes verification commands and creates test
scripts, fixtures and receipts. Codex uses `workspace-write` with
`approvalPolicy: never`, not the ordinary read-only review profile or
unrestricted access. Writable roots are the dedicated review checkout and
`raw/review/<repo>/<pr>/verification/<head>/`; put generated files in that
artifact directory so they persist without dirtying source or blocking the
next round. The cell receives the configured test scope and environment id.
On Windows it uses the session-local `unelevated` sandbox fallback; global
account settings and sandbox security files are not manually modified.
Inherited MCP/apps/plugins/computer-use and escalation tools are disabled.
Tracked implementation changes stop completion for user inspection. The
server's configured execution receipts and final gate remain mandatory;
reviewer output alone cannot certify product behavior.

## Repository contract

Commit `verification.json`, the API/data contracts it references, and the
verification scripts. Each repository owns its own complete list of major
user flows, assertions, impact paths and execution commands. There is no
global default list and no automatic claim that an incomplete list is complete.

```json
{
  "version": 1,
  "contracts": ["docs/api.md", "docs/test-data.md"],
  "prose_paths": ["docs/guides/*", "README.md"],
  "flows": [
    {
      "id": "save-record",
      "title": "Create a record and see it after reload",
      "kind": "browser",
      "command": "python scripts/verify_save_record.py",
      "paths": ["web/*", "api/*", "scripts/*", "verification.json"],
      "environments": ["api", "dataset", "settings"],
      "assertions": [
        {"id": "persisted", "expected": "Saved record remains visible after reload"}
      ]
    }
  ]
}
```

This is a format example, not a runnable substitute for the repository's
scripts or its full major-flow checklist. Use `api` for real API scenarios,
`browser` for actual browser interactions and API/UI results, and `command`
for a scenario that needs neither. The independent reviewer checks that the
list covers the repository's major behavior and the changed requirements.

Paths use `fnmatch` patterns: `*` crosses directories. Include shared
dependencies and supporting test scripts in every flow they affect. A path
not covered by any flow, an unreadable diff or a shared dependency file
invalidates every flow. A first review executes all major flows. Later
commits execute affected flows and record the exact reason for carrying
unaffected results forward. Renames include the removed and added paths.

## Local setup

1. Prepare a dedicated test account/dataset and a local UTF-8 `.env` file
   without a byte-order mark. Git-ignore `.env` and `.wiki/verification*`
   in both the project and review worktree before saving local configuration.
2. Select the cloud PR in the app's review-loop picker, choose
   `Claude Code Cloud`, and start. Missing prerequisites leave it stopped
   with an environment-preparation reason.
3. In its Review tab, open the project verification settings. Specify the
   environment label, permitted test account/data scope, absolute `.env`
   source path, browser tool, test API origins and environment revisions.
   Inspect the flow commands, then save. Optional setup and cleanup commands
   prepare dependencies and remove this review's test data/servers.
   The cloud handoff instruction button shows what the cloud implementer must
   put in the repository and PR body, bound to the current remote head. Missing
   prerequisites show `로컬 검증 준비`; review failures show `외부 수정 대기`.
4. Set the GitHub required check using the Review tab's button. It adds
   `wiki-agent/local-verification`, requires an up-to-date base and enables
   protection for administrators. It preserves existing required checks,
   review requirements and other protections. A branch with no protection
   receives a new rule. API/auth/plan limitations remain a visible setup
   failure, never a silently weakened gate.
5. Resume local verification. The server runs configured commands in the
   PR's dedicated local worktree; the independent verification reviewer checks
   their receipts, the code diff and the applicable review profile.

Local settings are saved in `.wiki/verification.local.json`, excluded from
Git. Cloud cannot supply them through a PR. Saving binds approval to the
exact `verification.json` digest; changing its commands requires inspecting
and saving the new configuration. The `.env` copy must also be Git-excluded.
An unrelated existing `.env` is never overwritten. A copy the server owns
can be refreshed when its local source changes.

The local settings contain `environment_id`, `test_scope`, `env_file`,
`browser_tool`, `allowed_origins`, `revisions`, `manifest_digest`, optional
`setup`/`cleanup`, and optional `redact_values`. Revisions are public labels
for the API deployment, dataset and settings each flow uses. Update them
when those external inputs change; the program cannot observe an external
API deployment or dataset replacement by itself. Source `.env` changes,
local execution settings and dependency locks also invalidate evidence.

## Registered local and Windows native collection

Local and external tasks use the same approved manifest and local settings.
After the existing offline and frozen-preservation checks, the authorized loop
collects only its enforced flow IDs. A plan-only artifact cannot launch the future
application. The existing final gate still runs after independent review.

Execution binds receipts to the specification, head/base, selected contract,
manifest, commands and environment. Setup and flow attempts are persisted before
dispatch. `WIKI_VERIFICATION_ARTIFACTS` points to an attempt directory under the
system temporary directory, outside the source checkout. Keep logs and fixture
records there. Cleanup has a 30-second bound; its failure holds readiness.
Windows setup processes stay in owned kill-on-close jobs until cleanup finishes.
Cancellation kills owned process trees. Restart never reuses unfinished proof or
kills a process by a stale recorded PID.

Native flows require manifest version `2`, local settings version `2` and the
fixed `wiki-agent-native` command. Version `1` remains valid for API, browser and
command flows, and rejects native fields. The first native runtime is Windows
Win32 with direct child control IDs. It launches a disposable app on an
attempt-owned Win32 desktop; it never switches the user's input desktop or
attaches to an existing app. Unsupported hosts, selectors and build identities
stay preparation failures. This does not make Orca a runtime dependency.

Example native flow, inside the version-2 manifest:

```json
{
  "id": "native-save",
  "title": "Native save event",
  "kind": "desktop",
  "command": "wiki-agent-native",
  "paths": ["native_app.py", "verification.json"],
  "assertions": [{"id": "saved", "expected": "Saved"}],
  "native": {
    "application": "disposable-win32-fixture",
    "window_class": "WikiVerificationFixture",
    "arguments": ["native_app.py"],
    "build_inputs": ["native_app.py"],
    "actions": [{"assertion": "saved", "action": "click", "control_id": 1,
                 "observe_id": 2, "expected": "Saved", "event": "WM_COMMAND/save"}]
  }
}
```

The Review tab shows setup for ordinary tasks once collection is pending. Inspect
the complete native target/actions and approve the application, executable path,
SHA-256 and disposable profile. Its saved `native` object also contains
`version: 1`, `host: "windows-win32"`, `ownership: "launch-disposable"` and
`desktop: "attempt-owned"`. These are closed values, not an agent-selected driver.
The executable must be a reviewed build input inside the checkout, or an approved
`python.exe`/`pythonw.exe` running a reviewed `.py` entry point. Unattested external
compiled builds are unsupported. Git's checkout filters map measured input bytes
to reviewed blobs; receipts retain both actual-byte hashes and blob IDs.

The app must honor `WIKI_NATIVE_PROFILE` for disposable state and write required
event observations to `WIKI_NATIVE_EVENTS`. Each event is a JSON line containing
the actual `pid`, top-level `hwnd`, `control_id` and registered `event` name.
The runner accepts only registered `click` and `read_text` actions. Before each
input it measures process/executable, window class, selector and active/focused
input-queue identity on its owned desktop. Missing required events fail their
assertions. Native receipts contain OS/runtime, DPI, geometry, profile/revisions,
measured build inputs, actions and hashed artifact locators. Changed artifacts
invalidate readiness and merge eligibility.

This is native control/event evidence on an isolated desktop, not physical
keyboard, compositor screenshot, installed wiki-agent hook or real-account
acceptance. The sample fixtures are disposable verification inputs. A project
must register its own app, actions, build inputs and meaningful assertions.
Representative performance measurement remains a separate unresolved obligation
until registered coverage exists. Ordinary green assertions cannot satisfy it.

Implementation references: [Windows job ownership](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[desktop connection](https://learn.microsoft.com/en-us/windows/win32/winstation/thread-connection-to-a-desktop),
and [native button events](https://learn.microsoft.com/en-us/windows/win32/controls/bm-click).

## Cloud handoff

The PR body has exactly one `cloud-handoff` JSON block. Cloud updates its
full commit ID after every correction, preserving an accurate distinction
between completed checks and behavior that has not been tested.

```cloud-handoff
{
  "version": 1,
  "implementation": "claude-code-cloud",
  "head": "0123456789abcdef0123456789abcdef01234567",
  "summary": "Add record creation and reload behavior",
  "run": ["Start the API and frontend using the repository's test runbook"],
  "checks": ["Build and repository-only tests passed"],
  "unverified": ["Real test API persistence and browser reload"]
}
```

The implementation environment can be selected in the PR picker. An existing
cloud handoff also selects the cloud path; a cloud spec cannot be downgraded
to local by reopening the picker. An already reviewed local spec is not
silently converted into a different workflow.

## Execution evidence

The server sets `WIKI_VERIFICATION_HEAD`, `WIKI_VERIFICATION_ENVIRONMENT`,
`WIKI_VERIFICATION_SCOPE` and `WIKI_VERIFICATION_BROWSER` for each command.
Scripts load the worktree's `.env` using their project's existing mechanism.
Use only the specified test account and dataset. A script is responsible
for refusing a production target, restricting writes to that test scope,
and cleaning up resources it starts; configure cleanup for failures too.

Every command prints exactly one `local-evidence` JSON block, with every
configured assertion once. An exit code of zero without the receipt does
not pass. Print the receipt compactly near the end: the server preserves
the last 80 output lines.

```local-evidence
{
  "head": "0123456789abcdef0123456789abcdef01234567",
  "flow": "save-record",
  "environment_id": "test",
  "test_scope": "review-fixtures",
  "browser_tool": "project-browser",
  "build_head": "0123456789abcdef0123456789abcdef01234567",
  "observations": [
    {
      "id": "persisted",
      "expected": "Saved record remains visible after reload",
      "actual": "Created test record appeared again after browser reload",
      "pass": true
    }
  ],
  "requests": [
    {"method": "POST", "url": "http://127.0.0.1:8080/records", "status": 201},
    {"method": "GET", "url": "http://127.0.0.1:8080/records", "status": 200}
  ],
  "actions": [
    {
      "action": "Fill the form, click Save, then reload",
      "expected": "Record is visible after reload",
      "actual": "Record was visible after reload"
    }
  ]
}
```

API and browser scenarios require actual request evidence from allowed test
origins. Browser scenarios also require action/expected/observed results,
the configured browser tool and the loaded build's actual commit ID.
The script must measure that ID from the served build, not merely copy the
requested commit into its receipt. Protocol validation checks completeness
and identity; the reviewer checks whether the script and observations prove
the behavior. A generated receipt is not an independent oracle.

If API access, authentication, the dedicated dataset or browser is missing,
print the same identity fields (`head`, `flow`, `environment_id`, `test_scope`)
and `"blocked": {"prerequisite": "dataset", "reason": "Test dataset is not prepared"}`
instead of observations. Allowed prerequisites are `api`, `dataset`,
`authentication`, `browser`, `setup` and `native`. This records preparation pending
without counting a functional failure or cloud repair cycle. Scripts must
classify these conditions explicitly; a malformed receipt remains a failed
verification script.

Do not emit request/response bodies, credentials or private records into
receipts. Use nonsecret fixture labels and concise observations. The server
redacts known `.env` values, credential environment variables, credential-like
headers and explicitly configured `redact_values` before storing or sharing
logs. That does not identify arbitrary private dataset contents: scripts
must exclude them. GitHub receives sanitized failure evidence and pass/reuse
summaries; the app retains detailed local receipts and logs.

## Completion, failures and restart

| Result | Next action |
| --- | --- |
| Missing handoff, configuration, API access or dataset | Environment preparation; completion stays blocked |
| Flow, offline gate or independent review fails | PR receives reproducible sanitized evidence; cloud implements the fix |
| Same invariant fails at the initial head and two distinct repaired heads | Re-analysis required; record cause, evidence and next experiment before resuming |
| Same commit/environment fails and later passes | Unstable; retain both outcomes and investigate before completion |
| App or PC restarts | Preserve completed receipts, mark interruption and await user resume |
| New commit | Re-review the new commit; rerun affected flows and retain reuse reasons |
| Environment revision/configuration changes | Invalidate affected evidence; required status returns to pending |
| All required flows, review and final gate pass | Publish the exact commit's status and summary; enable merge |

Failed or stale runs cannot manufacture a pass. Retrying the same failing
commit does not consume a cloud repair cycle. Cloud verification never dispatches
a local implementation session, commits fixes or pushes changes back to cloud.
Ordinary failures use their existing implementation owner. A user authorizes the
review loop explicitly. Unchanged failures and same-identity fail-then-pass
observations require correction or recorded investigation before completion.

The Review tab owns the reviewer model and effort, displays the independent
reviewer's live progress, and offers the next review round after a correction
or an allowed review. Selecting `다른 환경` in the PR picker runs
independent review without dispatching a local fixer. Claude Code Cloud keeps
its additional handoff and execution-evidence requirements. External/cloud PRs
use a detached review checkout, leaving existing implementation branches alone.

Pure prose documentation changes retain their independent review and final
gate but are exempt from API/browser execution only when the unchanged
repository manifest explicitly designates them in `prose_paths`. Unclassified
Markdown, referenced API/data contracts, flow impact paths and executable
tool, web, workflow or project-rule paths always require local execution.
Changing the designation itself changes `verification.json`, so that PR
requires runtime verification and local approval of the new manifest.

Failed flows, offline gates and serious review outcomes retain sanitized
attempt evidence bound to the commit and local environment. A later pass
at that same identity cannot publish success until recorded investigation
covers those failed attempts. A later failure requires new investigation.
GitHub read outages preserve unconfirmed attempts without consuming repair
cycles. Failed comment writes remain preparation pending with the exact
sanitized handoff saved locally. Explicit resume retries that report before
executing further verification; delivery failure never claims cloud received it.

Before merging, the app rechecks commit, base, current environment evidence,
final gate and GitHub protection. Its background poll also invalidates
previously verified cloud results when a changed environment is observed.
GitHub's required commit status blocks new heads even before the local app
has seen them. Protection applies repository-wide, so the app publishes the
same status after a local implementation's existing review and final gate;
it does not add cloud runtime checks or a protection-setup prerequisite to
ordinary local/other-environment review. Required registered local flows still
need current receipts. Saving local execution settings
enables status publication on ordinary tasks, including unprotected branches.
GitHub still applies any configured protections when the person requests merge.

A Cloud task first reads the target branch's protection flag. An explicitly
unprotected branch shows a setup instruction for the Review tab instead of
misreporting a protection endpoint's expected 404 as a connection failure.
Unreadable branch/protection responses remain blockers, including permission
errors and protections supplied only through rulesets.

The initial GitHub integration uses classic branch protection. Existing
rulesets are not treated as proof of equivalent protection; configure the
required classic rule or keep verification pending. Repository administrators
can still change protection, and credential holders with status-write access
can publish statuses: this is a workflow gate within the repository's existing
trust boundary. See [GitHub branch protection](https://docs.github.com/en/rest/branches/branch-protection)
and [commit statuses](https://docs.github.com/en/rest/commits/statuses).

## Validation

`python -m pytest -q tool/test_local_verification.py` exercises the cloud
path with real temporary Git repositories, executed commands and a local
HTTP API. GitHub and model sessions are test doubles. Existing loop/spec
tests cover the unchanged local path and final gate. With an existing
Playwright Chromium installation and a built `web/dist`, the same test file
also drives the real app's settings, GitHub-setup and resume controls through
the actual local API endpoints. Real project browser
scenarios require the repository-specific tools and test configuration above.

On 2026-10-01, an isolated build of the same Tauri source was driven through
WebView2's debugging connection, without Orca computer use. The real Windows
PTY translated two output lines into Korean, preserved the blank line between
them, and excluded the wrapped PowerShell prompt and entered command. A real
Claude work session read fixture files and executed their check. Both Claude
and Codex produced translated, multiline progress in independent read-only
review cells through the real app API and installed Chrome. The reviewer
model was selected in the Review tab; its events did not enter the Agent log.
The live run exposed and verified fixes for wrapped prompt leakage and final
answer duplication when a stop hook arrives after the final text block.

This verifies transport and presentation, not a complete Cloud review cycle.
PR #5 of `project-codeit-mid` had a valid handoff at
`3b9e0a72e17d2aa5550b40186db93e8d21f2543c` and declared 14 verification flows,
but no approved local verification settings were present. No Cloud execution,
correction-to-next-round cycle, GitHub status write or merge was performed.
The Codex work session also executed the fixture successfully in the app's
normal bypass mode. Its approval-enabled mode produced real progress and
approval events but could not start the Windows shell: sandbox initialization
rejected access to the account's `sandbox_users.json`. That mode remains
failed; account and security files were not modified to bypass the failure.
Local receipts and screenshots are under `artifacts/live-review-*.json`,
`artifacts/native-live-*` and `artifacts/chrome-live-review-*`; these generated
artifacts are not committed. Test-owned desktop and agent processes were closed.

A subsequent live Chrome/app-API check used the actual Codex `gpt-6-sol`
review cell to call all three shell-free source tools. It read the real PR's
`verification.json` and counted its 14 flows without approval events. Closing
the Codex process and resuming the same thread also successfully read that
file without approval events. The receipt is
`artifacts/live-codex-readonly-receipt.json`. This closes the reviewer's file
access gap, not the missing project runtime verification configuration. It
does not repair the separate Windows sandbox failure in approval-enabled
write sessions or grant reviewers unrestricted access.

The read-only profile above applies only to ordinary review. After the Cloud
execution requirement was clarified, a separate live Chrome/app-API check
used the actual Codex `gpt-6-sol` Cloud review cell to create a UTF-8 Python
verification script and execute it. The script asserted the real PR manifest's
14 flows and unique ids, wrote a JSON receipt, and emitted `CLOUD-EXEC-PASS`
with exit 0. Reopening the same thread executed the script successfully again.
Both turns had zero approval events; tracked source stayed clean. The record
is `artifacts/live-cloud-execution-receipt.json`. This proves real reviewer
file creation and command execution, not completion of the 14 product flows
or the still-unconfigured full Cloud verification cycle.
The final rerun also created, wrote and read a temporary file under the
configured verification artifact root, so ordinary temporary-file use stays
within the execution permissions rather than requiring another approval.
