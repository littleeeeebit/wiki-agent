# Mobile companion

The PC owns projects, conversations and agent execution. A paired phone opens
the same server in its browser, checks tasks and changes, and sends instructions
or answers approvals. It connects over mobile data or another Wi-Fi network
without a VPN or router port forwarding. Both networks must permit the relay
connection, and the PC and app must remain awake and online.

## Connect a phone

1. Update the app dependencies and build the screen, then restart wiki-agent.

   ```powershell
   python -m pip install -r requirements-chat.txt
   npm --prefix web ci
   npm --prefix web run build
   ```

   If the app uses `.venv`, use `.venv/Scripts/python.exe` on Windows or
   `.venv/bin/python` on macOS/Linux for the first command.

2. On the PC, open Settings → Mobile (`설정 → 휴대폰`) and press
   Enable external connection (`외부 연결 켜기`). The app prepares its tunnel
   runtime automatically and creates the pairing QR when the public path is
   ready. No separate `cloudflared` installation, administrator prompt or DNS
   setting change is required. The first download can take longer; preparation
   and retry progress appear in Settings, and Cancel remains available.
3. Scan the connection QR with the phone camera and open it in the browser.
   A phone app is optional. The browser can connect even when this PC has no APK.
4. For the optional Android app, scan the installation QR in Android app installation
   (`Android 앱 설치 (선택)`) with the phone camera. On the opened page, press
   Download APK (`APK 다운로드`), open the downloaded file and confirm
   installation. If prompted, allow installation from the current browser.
5. Use the automatically created pairing QR, or press Create pairing link
   (`연결 링크 만들기`) for another. In the Android APK, press
   Scan connection QR (`연결 QR 스캔`) and scan it. For the browser companion,
   scan it with the phone camera and open the link. Copy link remains available
   as an APK clipboard fallback. Open it within five minutes. Each QR/link
   works once; creating another invalidates the previous unused link. Anyone
   who redeems it can control the PC's work, so keep it private.
6. As a browser alternative, open Settings → Mobile (`설정 → 휴대폰`). Press Install app
   (`앱 설치`) when the browser offers it. On iPhone or iPad, use Safari's
   Share → Add to Home Screen (`공유 → 홈 화면에 추가`). The installed app opens
   without browser chrome.
7. In APK 0.1.3, use the top-left ⋮ menu to select Portrait (`세로 모드`) or
   Landscape (`가로 모드`). Selection requests the app's screen rotation and
   its matching mobile layout, even with system auto-rotate off. The choice
   persists; rotating the phone does not automatically select another mode.
   Portrait uses Tasks, Conversation and Selected task below. Landscape shows
   the task list, conversation and selected task together. Use List (`목록`)
   to fold the list and Together, Conversation or Task (`함께`, `대화`, `작업`)
   to choose how much of the workspace to show.
   Selecting a task opens its agent and review. The native desktop terminal
   stays in the desktop window. To disconnect every phone, use Disable
   connection and revoke devices
   (`연결 끄기 · 기기 해제`) on the PC.

Pairing lasts up to 30 days at the same hostname. The default is a temporary
Cloudflare URL: starting the tunnel again changes the address and requires a
new link. Cloudflare describes these tunnels as development services without
an uptime guarantee; a fixed named tunnel is the production option.
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

The app pins `cloudflared` 2026.10.0 and its
[official release SHA256 digests](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.0).
An installed executable is reused only when it matches the pinned digest;
otherwise the app downloads and verifies a private copy under
`raw/mobile-runtime/2026.10.0`. Windows x64/x86, macOS Intel/Apple Silicon and
Linux x64/ARM64 are supported; Windows ARM uses its x64 emulation. Cancellation
during a download cannot enable a tunnel afterwards. The process uses an isolated
empty config and excludes inherited `TUNNEL_*` settings, so another tunnel on
the PC cannot change the app's public path. A failed QUIC startup automatically
retries HTTP/2. The normal connection remains explicitly opt-in.

The app verifies the public HTTPS status before offering pairing. If the PC's
DNS cannot resolve the temporary hostname, it automatically queries Cloudflare
[DNS over HTTPS](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/)
using bootstrap IPs, then connects using the original TLS
hostname and certificate verification. Only public IPs are accepted. This
fallback changes neither OS DNS nor other applications; the phone still resolves
its address through its browser/network. The TLS trust store supplements OS roots
with the app's bundled CA package. DNS, certificate and public-path failures
are reported separately and recorded in the existing local `raw/errors.jsonl`.
GitHub downloads and Cloudflare connectivity must be permitted by the network;
an offline PC or a network blocking the relay cannot be made reachable by this
button. No physical teammate-PC acceptance is implied by automated checks.

The phone's network can change while the PC keeps executing. If the screen
loses its connection, press Reconnect (`다시 연결`) to reload saved records
and reattach to active turns without reloading the page or discarding drafts.
Returning online or bringing the screen back to the foreground also recovers
the shared feed and snapshot. An instruction whose delivery is uncertain is
never automatically resubmitted.

The installed shell caches only a connection-help page. Conversations and API
responses remain on the PC and are not available offline. An app installed
from a temporary `trycloudflare.com` address belongs to that address and stops
reaching the PC after the address changes. Use the fixed-address setup below
for an app that survives desktop restarts.

## Android APK

The native shell under `android/` produces a directly installable APK. It scans
the same one-use QR without requesting camera permission, validates that the
result is a root HTTPS pairing link, and loads only that origin in an isolated
WebView. External links leave the app; mixed content, file access, third-party
cookies, gesture-free popups and invalid TLS are refused. New-window link
clicks use the existing navigation guard, which sends external destinations
to the browser instead of creating another WebView. There is no JavaScript bridge.
The WebView keeps the pairing cookie but disables its HTTP response cache;
conversation and API responses remain on the PC.

Backup and device-transfer rules exclude app data, including saved origins and
WebView cookies, on Android 12 and newer. The older backup path is also disabled.

The QR scanner is the Google Play services code scanner. It may download its
scanner module on first use. A Clipboard link (`클립보드 링크 연결`) fallback
is available when Play services is unavailable.

Install JDK 17 or newer and Android SDK platform 36 with build-tools 36.0.0,
then set `JAVA_HOME` and either `ANDROID_HOME` or `ANDROID_SDK_ROOT`. Build and
run the parser tests with:

```powershell
powershell -ExecutionPolicy Bypass -File tool/build_android.ps1
```

The outputs are the ignored local release files `raw/android/wiki-agent.apk`
and `raw/android/wiki-agent.aab`. They are runtime data, not scratch: on
2026-10-09 the after-merge cleanup of `artifacts/` (`scratch_dirs`) deleted the
served APK, so the installation QR vanished and its page returned 404. Version 0.1.4 targets API 36, retains the
Android 8 minimum, disables debugging and signs the APK with v2 and v3 schemes.
Android 15 on Galaxy S21 meets these requirements; a higher target API does
not require the phone to run that newer OS. The build runs release parser
tests and Android lint, verifies the APK signature/alignment and rejects
debuggable builds or permissions beyond internet and network-state access.

When that file exists and the external connection is ready, the desktop's
Mobile settings displays an installation QR. The QR opens `/mobile-install`
at the current public HTTPS origin. Its download button serves only that
fixed APK at `/mobile-install.apk`, with the Android package MIME type and
attachment filename `wiki-agent.apk`. A missing build shows preparation
guidance instead of a QR and returns 404 for the download.

Installation does not require a pairing cookie, consume a pairing link or
grant access to private APIs. Its QR contains no pairing secret and remains
usable while the current external connection is running, even after a
pairing link expires. After installation, scan the separate one-use connection
QR inside the app. Android still requires confirmation to install the file.

Version 0.1.3 added explicit screen modes to the overflow menu introduced in
0.1.2. Version 0.1.4 routes new-window PR/citation links through the existing
navigation guard instead of leaving them inert. Multiple windows are disabled
and gesture-free JavaScript window requests stay blocked, following
[Android's WebSettings contract](https://developer.android.com/reference/android/webkit/WebSettings#setSupportMultipleWindows(boolean)).
Update the installed release from the same installation QR;
the application ID and release certificate are unchanged, so uninstalling is
not part of this update. The saved connection and cookies are not deliberately
cleared by the update. An older APK does not gain screen-rotation controls
merely by reloading the web screen: install the new APK in place.

USB installation remains an alternative:

```powershell
adb install -r artifacts/wiki-agent.apk
```

The build script creates and reuses a local RSA-3072 signing key under
`raw/android-signing/release.p12`. Its random password is protected with
Windows DPAPI in `password.dpapi`, readable by the same Windows user on the
same PC. Both stay outside Git and HTTP downloads. Do not delete the signing
directory: updates need the same key. Preserve a protected backup before
moving machines. To use an existing private signing key instead, set
`WIKI_ANDROID_KEYSTORE` and `WIKI_ANDROID_KEY_PASSWORD`; the alias must be
`wiki-agent`. The script restores these process variables after building.

The former debug-signed APK and this release have different certificates.
If a debug build was successfully installed, Android will refuse an in-place
update with the release key. Export anything needed before deliberately
uninstalling the debug copy. A failed installation alone is not evidence of
a certificate conflict.

### Galaxy S21 and installation protection

On 2026-10-03 the user reported Galaxy S21, One UI 7.0, Android 15 and the
message “App blocked to protect your device” followed by “App not installed.”
The subsequent phone screenshot identifies Google Play Protect: it says that
apps from this developer have not been scanned and offers Install anyway
(`무시하고 설치하기`) above the blue acknowledgement button (`확인`).
This screenshot does not establish a malware finding or an unsupported Android
version. If the user trusts the APK received from their own PC, that per-install
choice allows them to proceed past this warning without disabling Play Protect.
It is not Google approval or proof of safety. Prefer a scan if offered; if the
wording differs, the option is absent or installation still fails afterwards,
capture the new error rather than assuming this instruction applies.
[Google's Play Protect guidance](https://support.google.com/googleplay/answer/2812853)
recommends keeping protection enabled and describes scanning unfamiliar apps.

The user subsequently confirmed successful installation and PC connection on
the Galaxy S21. That closes the reported installation/connection failure for
the build they tested; it is not Google security approval or physical-device
acceptance of the later 0.1.2 or 0.1.3 UI updates. No protection-disabling instruction,
VirusTotal upload or appeal submission was needed for that confirmation.

A valid release signature proves package integrity; it does not grant store
approval or override protection. Keep Play Protect and Samsung protection
enabled. Publishing through Google Play or Galaxy Store requires the owner's
developer account and the store's review. If Play Protect flags the app,
investigate its classification and submit an appeal rather than treating a
new certificate or higher SDK as a fix. The build now supplies a signed AAB
for a Play Console release/test track, but no store publication or security
approval has happened. Current Play submission requirements are documented
in [Android's target API requirements](https://developer.android.com/google/play/requirements/target-sdk).

Before the screenshot, a classification review was selected prematurely.
An appeal is not established as necessary by this unfamiliar-developer warning.
A local, unsubmitted draft at `artifacts/play-protect-appeal.txt` held
candidate APK hashes, signing certificates and declared permissions; the same
2026-10-09 scratch cleanup deleted it, so regenerate those values from the
current APK if an actual classification issue needs investigation later. The phone's actual
file hash remains unverified. Its served branch, commit, frontend bundle and
launcher worktree are unknown; the screenshot does not identify them. Google's
[appeal form](https://support.google.com/googleplay/android-developer/contact/protectappeals)
requires a contact email and the SHA256 of an APK uploaded to VirusTotal.
[Standard VirusTotal submissions](https://docs.virustotal.com/docs/private-scanning)
share samples with security vendors and the community. No sample upload or
appeal submission has been made; those require the owner's disclosure decision
and submission details. Do not report the new release as blocked until that
has actually been observed on the device.

The APK itself survives a temporary tunnel hostname change. The saved address
does not: enable the desktop connection, make a new link and use Scan
connection QR (`연결 QR 스캔`) again. A fixed address avoids that re-pairing.

## Phone workspace

Portrait shows one focused content view with bottom navigation. Landscape is
a compact version of the PC workspace: a 180px task list, conversation and
selected task side by side. List (`목록`) folds the task list. Together
(`함께`) restores both content panes; Conversation (`대화`) and Task (`작업`)
focus either one. With the list folded, a focused pane uses the full width.
Focus and task tabs stay inside their respective panes, rather than competing
for space in the shared header. Old web Landscape preferences do not select
a native screen mode. Only remote clients enter the landscape layout;
the PC's existing local layout and width breakpoints are unchanged. Switching
views or screen modes keeps conversations mounted and preserves unsent drafts.
The native terminal remains desktop-only.

The compact header names the project or task and keeps Settings reachable.
Conversation offers Map there rather than another header. Task options
(`작업 옵션`) reveals model, usage and specification details. Specifications
ready to start and active planning questions stay visible. Project/review
tools and code diff contents are disclosed on demand. Stop, approvals and
active session permission revocation are not hidden with model settings.

After pairing, APK 0.1.3 has a native overflow menu
(`앱 메뉴 · 화면 모드 및 연결`) instead of a second app bar. It offers
Scan connection QR, Clipboard link, Reload, Portrait and Landscape. Both
screen-mode items are always enabled. A single saved native choice feeds
`setRequestedOrientation` and the same-origin web layout hint. No rotation
sensor, orientation media query or system auto-rotate setting picks the mode;
opening the keyboard cannot switch it. The browser companion cannot rotate
its host browser's Activity and does not offer these APK controls.
The WebView origin validation, permissions and release certificate are unchanged.
The native activity handles orientation and screen-layout changes without
recreation, retaining its WebView and active requests. Android
11 and newer use the union of system-bar, cutout and keyboard insets so the
WebView has the remaining usable height; older phones retain resize handling.
These native changes still require a real-phone rotation and keyboard check.

The requested orientation is for a full-screen phone such as Galaxy S21.
Android can override orientation requests in multi-window or on large-screen
devices; this is not a guarantee for tablets or managed displays.
[Android orientation restrictions](https://developer.android.com/develop/adaptive-apps/guides/app-orientation-aspect-ratio-resizability)

## Fixed address

For an existing named Cloudflare Tunnel and domain, start the server with its
public HTTPS origin. The operator owns this named tunnel's lifecycle.

```powershell
python tool/main --port 8787 --mobile-origin https://wiki.example.com
```

Configure the hostname's tunnel service as `http://127.0.0.1:8787` and its
HTTP Host Header as `mobile.wiki-agent.invalid`. In a local tunnel config:

```yaml
ingress:
  - hostname: wiki.example.com
    service: http://127.0.0.1:8787
    originRequest:
      httpHostHeader: mobile.wiki-agent.invalid
  - service: http_status:404
```

Keep the tunnel's existing name and credentials configuration, then follow
Cloudflare's [named tunnel instructions](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/).
The origin must match the phone's address exactly. Disabling mobile access
invalidates every pairing token even when the tunnel is managed separately.
Restart the server with the flag to enable that fixed origin again.

## Access and transport

The Python listener stays on localhost. The tunnel overwrites `Host` with a
dedicated value; forwarded requests cannot claim local desktop privileges.
Other hosts and cross-origin writes are rejected. Remote API requests require
a signed, expiring `Secure`, `HttpOnly`, `SameSite=Strict`, host-only cookie.
The signing key stays in the ignored `raw/chat/mobile-key`. Revocation rotates
it and closes active companion sockets.

The pairing secret travels in a link fragment, removed from browser history
before pairing. It is not part of an HTTP request URL or relay access log.
The QR code is rendered locally with `qrcode.react`; no third-party QR service
receives the pairing link. It uses the same expiry and single-use secret as
the copyable link and disappears when its displayed lifetime ends.
Authenticated application responses use `Cache-Control: no-store`. The relay
terminates HTTPS and can observe traffic; this is not end-to-end encryption
between phone and desktop.

Remote API traffic uses WebSocket binary frames because temporary Cloudflare
tunnels do not support SSE. The bridge invokes the existing HTTP routes and
middleware, preserving project ownership and event ordering. It never accepts
a client-selected Host, Origin or cookie header. Local desktop requests still
use HTTP. The added Python dependency is Uvicorn's `wsproto` implementation.

Conversation writes and clears announce identifier-only invalidations through
the shared `/api/loops/events` feed. Clients reread their active focus through
the existing project guard and attach to an externally started question's run.
Their own streaming answer is not overwritten; completion rereads its record.
Work completion/clear and queued-instruction or session-permission changes also
announce invalidations. Reconnecting sends the last accepted feed sequence and
refreshes records, running work/review turns and task lists. A cursor from an
old process is clamped to the new buffer. Reconnect never retries a write.

The 2026-10-03 sync fix changes Python as well as the screen. Restart the PC
app to load it, then reload the phone's web screen. The existing APK 0.1.3
can load the web/sync update without a new native installation. Install APK
0.1.4 in place for the external-link fix. A temporary
tunnel restarted with the app needs a fresh pairing QR.

## Verification

```powershell
python -m pytest -q tool/test_mobile.py tool/test_mobile_transport.py tool/test_main.py tool/test_sync.py tool/test_android_shell.py
python web/tests/mobile_browser.py
python web/tests/mobile_sync.py
python web/tests/mobile_browser.py --live-tunnel
python web/tests/mobile_connection.py
powershell -ExecutionPolicy Bypass -File tool/build_android.ps1
```

On 2026-10-07, `mobile_connection.py` passed with an empty runtime cache, no
installed tunnel executable, no APK and forced desktop DNS failures. One click
downloaded and verified the official executable, recovered address resolution,
created the pairing QR and connected a separate browser through real public
HTTPS. The authenticated cookie, WebSocket API and saved pairing after reload
also passed. This publishes only synthetic fixtures; it does not verify the
reported teammate PC or a physical phone. The existing mobile browser/layout
checks and affected Python checks passed. The full debt ratchet still reports
five unchanged pre-existing files outside this change.

The browser script needs the installed Playwright package and Chromium. It
starts a separate synthetic fixture server; the live option publishes only
that fixture and stops its own tunnel afterwards. Checks cover 320px and
400px phones, explicitly selected portrait and landscape, ignored old preferences,
QR regeneration, desktop, touch targets, input sizes, pairing, reload
and streamed requests through a real HTTPS tunnel. Checks measure task reading
space, simultaneous landscape panes, rail folding and focused panes,
secondary-settings and diff disclosure, draft preservation, RTL pane order
and a reduced-height viewport. A native hint is simulated: browser
tests do not rotate Android itself. Resizing never selects another mode;
invalid modes and APK hints in local PC windows are ignored. The script can
record PC geometry with `--capture-desktop` before changes and compare it
on subsequent runs. Release unit tests check both explicit orientation/layout
pairs and stored-mode fallback. Real keyboard behavior, native menu taps, 200% browser
zoom and physical safe areas are not verified by those fixture checks.
Installation checks cover
the separate QR, responsive download page and attachment bytes; the live
option downloads a 2 MB synthetic APK through HTTPS before pairing. API tests cover expired
or replayed links, forged cookies, origin and project guards, stream ordering
and revocation, as well as unpaired APK downloads without private API access.

The sync script uses two independent browser contexts, actual query records
and event producers, a stub model, and authenticated WebSocket requests through
a loopback-only HTTPS relay. It verifies live partial answers in both directions,
focus isolation, manual feed reconnect without losing a draft or resending a
question, online recovery into an externally started active run, and cross-client clear.
It needs OpenSSL (Git for Windows includes
it), Playwright and Chromium. It does not publish a tunnel or touch user records.
`tool/test_android_shell.py` guards the native window/navigation configuration
in source; it does not execute Android's WebView. On a device, tap a PR or
citation link that requests a new window and confirm the external browser
opens while the paired app and draft remain available.

Physical-device acceptance remains necessary: install the APK and scan a live
pairing QR; with system auto-rotate off, select Landscape from portrait and
verify the screen turns and shows the compact three-pane workspace; select Portrait
back and verify bottom navigation returns. Repeat with auto-rotate on, rotate
the phone without selecting a mode, and confirm the explicit choice stays.
Reopen the app and confirm its saved mode. Both menu items must stay enabled.
Type with the keyboard visible, retain drafts across mode changes, and check
cutouts and gesture areas. Open the browser alternative on iOS Safari and
Android Chrome; add the PWA to the home screen; then
change between Wi-Fi and mobile data and reconnect. Browser emulation and an
APK signature check do not verify those platform behaviors.
