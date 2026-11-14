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
| `build`, `output` | The command that builds the app from the checkout, and the checkout-relative path of the exe or APK it produces |
| `origins` | The app's own origins; a navigation or new target outside them is refused |
| `calls` | The origins of the app's native bridge, such as Tauri's `http://ipc.localhost`; requests to them count as calls |
| `package`, `activity` | Required on Android, refused on Windows |
| `build_inputs` | Tracked files or globs, expanded against `git ls-files` at the reviewed head and measured like today |
| `actions` | Checkpoints `{assertion, expected, selector, no_requests}`; they cover each registered assertion exactly once, as now |

The scenario script holds the steps that drive the screen. It does not judge
them. At each checkpoint the collector reads the registered `selector` itself,
over its own DevTools session, and compares that text with `expected`. The
selectors and the zero-work claims sit in the manifest, so the reviewer sees
exactly what is observed.

Every request the collector sees during the whole run must go to a registered
origin: the app's own `origins`, the local settings' `allowed_origins`, or
`calls`. A request to any other origin fails the flow closed, whatever the
checkpoints say. The existing receipt contract treats an unapproved API origin
the same way.

A checkpoint's interval runs from the scenario's `begin` message to the
collector's read; the proxy logs both on the collector's own clock. When
`no_requests` is set, the collector's network log for that interval must show no
request to `allowed_origins` or `calls`. Requests to the app's own `origins`,
such as its assets, do not count against it. A bridge that the collector cannot
see as a request cannot be named in `calls`. Preflight refuses a `no_requests`
checkpoint on an app whose `calls` is empty, because nothing there could prove
zero work.

The `build` command and `output` path are part of the manifest, so the existing
manifest approval (`manifest_digest`) covers them. The collector runs `build` in
the clean checkout at the reviewed head, measures `output`, and launches exactly
that file. A prebuilt exe or APK outside this build is never launched. This is the
WebView form of the Win32 rule that an executable must be a measured reviewed
build input.

### Local settings: one approval per application

`LocalSettings.native` becomes a list of approvals keyed by `application`, at
settings version 3. Version 2 with a single Win32 approval stays readable. A
WebView approval names the host (`windows-webview2` or `android-emulator`), the
Node that runs scenarios with its SHA-256 and the profile. For Android it also
names the `adb` and `emulator` executables with their SHA-256 and the system
image folder with a content digest over its files (sorted relative path and
SHA-256 of each). Preflight measures all of them again, and they enter the run
signature, so replacing any of them at the same path revokes earlier passes. It
approves no app binary, since the app is built from the head. Approving a new application still goes through the existing setup
approval; nothing is approved by being present in a manifest.

Every current reader of the single approval moves to one lookup by application
and host: the run signature, preflight, the runner request, receipt validation,
evidence reuse, the merge-time check and `set_config`. The Review tab form shows
one approval group per desktop application and saves version 3.

Until a driver's collector exists, its flows fail closed. Preflight refuses such
a flow with a stated reason before anything runs: no setup command, no build, no
collector, no review dispatch. The task stays stopped at local verification
preparation until the collector lands. Step 1 carries a regression that proves
this on a real run.

### Windows collector

1. Preflight before any mutation: Node hash, measured build inputs, backend and
   screen revisions, then `build` at the reviewed head and the hash of `output`.
   It also creates fresh app data and WebView2 user-data folders owned by this run.
2. Launch that `output` on the owned desktop in the kill-on-close job. The WebView2
   remote-debugging port is bound to loopback and set for this process only.
3. Accept the debugging endpoint only after confirming that its browser process
   descends from the launched pid.
4. The collector holds that DevTools connection itself. The scenario connects
   only to a collector-owned proxy, which logs every message. The proxy refuses
   `Target.createTarget`, attaching to other targets, and navigation outside
   `origins`. The collector's own session records network events and performs
   the checkpoint reads.
5. Record the native identity (pid, window handle, class), the built output's
   hash and head, each checkpoint's collector-read value, the proxy and network
   logs, screenshots and artifact hashes.

### Android collector

1. Create an AVD from the approved system image under an attempt-owned
   `ANDROID_AVD_HOME`. Boot it with `-wipe-data` on a console port that no
   existing device uses, which fixes its serial as `emulator-<port>`. Refuse if
   that serial already appears in `adb devices`. Then confirm that the serial
   reports `ro.kernel.qemu=1` and that its emulator process is the one launched.
2. Every `adb` call and forward names that serial with `-s`; nothing runs
   against a default or physical device.
3. Build `output` at the reviewed head, install that APK, and confirm that the
   installed package's path digest matches the measured file.
4. Start `package/activity`, confirm the foreground activity and the app's pid,
   then forward only that pid's WebView debugging socket. The same
   collector-owned proxy and checkpoint reads as on Windows apply.
5. After a navigation or restart, wait for the target and reacquire it by pid.
   A restart checkpoint restarts the app without clearing storage.
6. Record the emulator pid, serial, AVD home and forwards, plus the Windows
   fields with package, activity and APK digest. Then remove the forwards, kill
   that emulator pid and delete its AVD home.

### Receipts and cleanup

Both collectors write receipts with `driver: "webview-v1"` and a closed field
set that `validate_receipt` checks the same way it checks Win32 receipts. A
WebView receipt cannot satisfy a Win32 target, and the reverse is refused too.

Cleanup is proven on every outcome: success, a failed assertion, a preparation
failure, a timeout and a cancellation. The collector writes its ownership record
before its first mutation, ahead of the build and of any folder it creates. It
then adds each resource just before creating it: processes, profile folders, AVD
home, emulator pid, serial and forwards. The record lives in the flow's artifact
folder, so it outlives the collector.

A pid alone is not an ownership token, because Windows reuses pids once a
process exits. Each recorded process therefore carries a stable identity: its pid,
its creation time, its executable path and SHA-256, and its parent pid as
observed at launch. An emulator also carries its console serial.

The orchestrator owns the end of that record. When the collector exits on its
own, it has already cleaned what it recorded. When the collector is killed on a
timeout or a cancellation, the gate's process-tree kill stops its processes but
not its folders or `adb` forwards. The orchestrator then reads the persisted
record and cleans exactly the resources listed there, with a bounded time. Before
it signals a live pid, it reads that process's creation time and executable
again. It acts only when both match the record. Any mismatch means the pid now
belongs to another process. That process is left running, and the recorded one
counts as exited. It removes recorded forwards with
`adb -s <serial> forward --remove` only while that serial still answers as the
recorded emulator. It deletes recorded folders only inside the artifact folder.
It touches nothing the record does not name. Only then does it validate that the
record reads `cleaned`.

Today `receipt` raises on a `blocked` result before any native validation runs,
so this changes too. A desktop flow's outcome, blocked, timed out or cancelled
included, is accepted only after that validation. A missing record, or a
resource still present after cleanup, holds the flow as blocked and names what
is left, whatever the scenario reported. The rule applies to the Win32 driver as
well.

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
| 1 | Manifest variant | `driver` union, Win32 default, WebView fields and validation; settings version 3 with per-application approvals; current manifests and settings still pass; a real run proving a WebView flow fails closed before any setup, build or review | In progress |
| 2 | Windows WebView2 collector | Head build and launch of `output`, process-tree check of the endpoint, collector-owned proxy and checkpoint reads, cleanup proven on every outcome; one real Tauri fixture run, plus refusals for a wrong pid or endpoint, a new target, navigation outside `origins`, a stale or prebuilt binary, a `no_requests` checkpoint that made a request or call, a request to an unregistered origin, a collector killed mid-run whose recorded resources the orchestrator then cleans, a recorded pid reused by another process that the cleanup leaves running, a failed cleanup, and a Win32 receipt offered for a WebView flow and the reverse | Planned |
| 3 | Android emulator collector | Owned AVD and serial, head-built APK identity, pid-bound attach, restart without data loss, cleanup proof; one real fixture run, plus refusals for an existing serial or physical device, a wrong pid, a replaced `adb`, emulator or system image, a killed collector whose forwards and AVD home the orchestrator then cleans, a reused emulator pid or serial that the cleanup leaves alone, and a failed cleanup, with a pre-existing emulator left untouched | Planned |
| 4 | Cross-driver checks | Mixed Win32/WebView manifests in one run, and the full hosted gate | Planned |
| 5 | PR #4 flows | `video-translate-overlay-2` registers its two flows against the new driver and its review passes local verification | Planned |

Step 1 alone already lets PR #4's manifest validate. Its review would still stop
at preflight, with the reason stated; only steps 2 and 3 produce evidence. Each
collector PR carries its own safety negatives, so it is safe to ship alone.
Steps 2 and 3 can run in either order. Step 5 happens in that project, not here.

## Runtime choices

Scenarios are Node `.mjs` scripts with a pinned DevTools client, connected to
the collector's proxy rather than to the app. The approval records the Node
executable and its SHA-256, as it does for any other approved runner.

The Android collector runs on this PC. The Android SDK and the approved emulator
system image live outside the checkout. The approval records their paths, and
the receipt records the system image's `source.properties` revision.
