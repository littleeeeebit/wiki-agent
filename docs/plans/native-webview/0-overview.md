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
| `package`, `activity` | Required on Android, refused on Windows |
| `build_inputs` | Tracked files or globs, expanded against `git ls-files` at the reviewed head and measured like today |
| `actions` | Checkpoints `{assertion, expected, selector, no_requests}`; they cover each registered assertion exactly once, as now |

The scenario script holds the steps that drive the screen. It does not judge
them. At each checkpoint the collector reads the registered `selector` itself,
over its own DevTools session, and compares that text with `expected`. When
`no_requests` is set, the collector's own network log for that checkpoint must
show no request to the app's backend. The selectors and the zero-work claims sit
in the manifest, so the reviewer sees exactly what is observed.

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
Node that runs scenarios with its SHA-256, the profile, and for Android the SDK
and emulator system image. It approves no binary, since the binary is built from
the head. Approving a new application still goes through the existing setup
approval; nothing is approved by being present in a manifest.

Every current reader of the single approval moves to one lookup by application
and host: the run signature, preflight, the runner request, receipt validation,
evidence reuse, the merge-time check and `set_config`. The Review tab form shows
one approval group per desktop application and saves version 3. Until a driver's
collector exists, a flow on that driver stops at preflight with a stated reason.
It never validates and is silently skipped.

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
failure, a timeout and a cancellation. The collector always writes an ownership
record of what it created: processes, profile folders, emulator, serial and
forwards. Today `receipt` raises on a `blocked` result before any native
validation runs, so this changes too. For a desktop flow, the orchestrator first
validates that the ownership record is `cleaned`, and only then accepts the
outcome. That covers a blocked, timed-out or cancelled run as well. A missing or
unclean record holds the flow as blocked with that reason, whatever the scenario
reported. The rule applies to the Win32 driver as well.

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
| 1 | Manifest variant | `driver` union, Win32 default, WebView fields and validation; settings version 3 with per-application approvals; current manifests and settings still pass | In progress |
| 2 | Windows WebView2 collector | Head build and launch of `output`, process-tree check of the endpoint, collector-owned proxy and checkpoint reads, cleanup proven on every outcome; one real Tauri fixture run, plus refusals for a wrong pid or endpoint, a new target, navigation outside `origins`, a stale or prebuilt binary, a `no_requests` checkpoint that made a request, and a failed cleanup | Planned |
| 3 | Android emulator collector | Owned AVD and serial, head-built APK identity, pid-bound attach, restart without data loss, cleanup proof; one real fixture run, plus refusals for an existing serial or physical device, a wrong pid, and a failed cleanup, with a pre-existing emulator left untouched | Planned |
| 4 | Cross-driver checks | A Win32 receipt for a WebView flow and the reverse, mixed Win32/WebView manifests, and the full hosted gate | Planned |
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
