# Native WebView verification — Tauri on Windows and Android

Purpose. Let a desktop flow prove behaviour of an app whose interface is a
WebView: a Tauri build on Windows (WebView2) and the same app on Android. Today
the native collector drives Win32 controls only, so such a project cannot
register its native flows at all and its review stops before the first round.

## Why this plan exists

`video-translate-overlay-2` PR #4 registered two native flows, one for the
Windows build and one for the Android build. Its review loop stopped at
`로컬 검증 준비`: the manifest failed validation with 46 field errors, all
inside `flows.2.native` and `flows.3.native`. PR #345 now shows those errors in
the stop reason; it does not make the flows valid.

The flows are not wrong for the app. They are wrong for the collector. On
2026-11-14 the user chose to extend host support rather than drop the flows, so
this plan adds a WebView target instead of weakening the project's acceptance.

## What exists now

`tool/native_verification.py` accepts exactly one target shape.

| Model | Fields | Meaning |
| --- | --- | --- |
| `NativeTarget` | `application`, `window_class`, `arguments`, `build_inputs`, `actions` | One Win32 window found by class |
| `NativeAction` | `assertion`, `action` (`click` or `read_text`), `control_id`, `observe_id`, `expected`, `event` | One control addressed by `GetDlgItem` id |
| `NativeSettings` | `host: windows-win32`, `executable`, `executable_sha256`, `ownership`, `desktop`, `profile` | The one approved executable |

The collector launches the approved executable on an isolated desktop inside a
kill-on-close job, sends only registered control messages, and writes a receipt
that `validate_receipt` checks against the target, the measured build inputs and
the ownership record. A WebView exposes no control ids, so none of this reaches
its interface.

`LocalSettings.native` is a single `NativeSettings`. A project with a Windows and
an Android target needs one approval per application.

## Design

### Manifest: a closed variant, not a looser model

`Flow.native` becomes a discriminated union on `driver`. The existing shape
gains `driver: "win32"` as its default, so every current manifest validates
unchanged and keeps `extra="forbid"`. The new shape is `driver: "webview"`.

| Field | Rule |
| --- | --- |
| `driver` | `"webview"` |
| `platform` | `"windows"` or `"android"` |
| `application` | The logical approval name, such as `vto-windows`; it selects the approval in local settings |
| `scenario` | A tracked script path, also listed in `build_inputs` |
| `package`, `activity` | Required on Android, refused on Windows |
| `build_inputs` | Tracked files or globs, expanded against `git ls-files` at the reviewed head and measured like today |
| `actions` | Named checkpoints `{assertion, expected}`; they cover each registered assertion exactly once, as now |

The scenario script holds the DOM steps. The manifest names only the checkpoints
the script must report, so a reviewer reads assertions without reading selectors.

### Local settings: one approval per application

`LocalSettings.native` becomes a list of approvals keyed by `application`, at
settings version 3. Version 2 with a single Win32 approval stays readable. A
WebView approval adds the host (`windows-webview2` or `android-emulator`), the
approved executable or APK with its SHA-256, the runtime used to run scenarios,
and for Android the emulator image. Approving a new application still goes
through the existing setup approval; nothing is approved by being present in a
manifest.

### Windows collector

1. Preflight before any mutation: executable hash, measured build inputs, backend
   and screen revisions, and a fresh app data and WebView2 user-data folder owned
   by this run.
2. Launch the approved exe on the owned desktop in the kill-on-close job, with
   the WebView2 remote-debugging port bound to loopback and set for this process
   only.
3. Accept the debugging endpoint only after confirming that its browser process
   descends from the launched pid.
4. Run the scenario against that endpoint with DOM locators. It may not open new
   targets or navigate outside the app's own origin.
5. Record the native identity (pid, window handle, class), each checkpoint's
   observed value, screenshots and artifact hashes.

### Android collector

1. Boot a disposable emulator from the approved image with a wiped data
   partition; never use a connected physical device.
2. Install the approved APK and confirm the installed package's signing digest
   and version match the approval.
3. Start `package/activity`, confirm the foreground activity and the app's pid,
   then attach to that pid's WebView debugging socket only.
4. Run the scenario. After a navigation or restart, wait for the target and
   reacquire it by pid; a restart checkpoint restarts the app without clearing
   storage.
5. Record the same receipt fields as Windows, plus package, activity and APK
   digest, then shut the emulator down and delete its data.

### Receipts and cleanup

Both collectors write receipts with `driver: "webview-v1"` and a closed field
set that `validate_receipt` checks the same way it checks Win32 receipts. A
WebView receipt cannot satisfy a Win32 target, and the reverse is refused too.

Cleanup is recorded independently of the scenario's outcome: a failing scenario
still has to prove the process tree, profile folders and emulator are gone. A
checkpoint that claims nothing happened, such as a cancelled delete, needs
request or call evidence from the scenario, not just an unchanged screen.

### What does not change

- The Win32 driver, its receipt fields and its strictness.
- Review routing, Jev selection and the review loop handoff. A WebView flow is
  one more desktop flow to them.
- No global keyboard input, no attachment to a process the collector did not
  start, no use of the user's input desktop or physical phone.

## Steps

Each step is a separate PR, approved before the next starts.

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Manifest variant | `driver` union, Win32 default, WebView fields and validation; settings version 3 with per-application approvals; current manifests and settings still pass | Planned |
| 2 | Windows WebView2 collector | Owned launch, process-tree check of the debugging endpoint, scenario runner, receipt and its validator; one real Tauri fixture run | Planned |
| 3 | Android emulator collector | Disposable emulator, APK identity, pid-bound WebView attach, restart without data loss, cleanup proof; one real fixture run | Planned |
| 4 | Negative checks | Wrong pid or endpoint, swapped driver receipts, unregistered navigation, failed cleanup, zero-work claim without evidence — each refused | Planned |
| 5 | PR #4 flows | `video-translate-overlay-2` registers its two flows against the new driver and its review passes local verification | Planned |

Step 1 alone already lets PR #4's manifest validate, but its review would still
stop at collection; only steps 2 and 3 produce evidence. Steps 2 and 3 can run
in either order. Step 5 happens in that project, not here.

## Open questions

- Scenario runtime: Node with a pinned WebDriver/CDP client, or Python. The work
  cell's draft uses a `.mjs` script; the collector then needs an approved Node.
- Whether the Android collector belongs on this PC at all, given the emulator's
  size and boot time, or only on a host that already has the SDK.
