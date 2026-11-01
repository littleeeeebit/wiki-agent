---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# android/ — the mobile APK shell around the paired web screen

## What it is for

A native Android shell for the mobile companion (`docs/mobile.md:106-116`).
It scans or pastes a one-use HTTPS pairing link, remembers that origin and
loads only that origin in a locked-down WebView. Execution stays on the
desktop. The APK adds no execution API and no JavaScript bridge.
One Gradle project, `wiki-agent-mobile`, with a single `:app` module
(`android/settings.gradle:17-18`). Package `dev.wikiagent.mobile`, minSdk 26,
targetSdk 36, version `0.1.4` (`android/app/build.gradle:6-15`).

## Entry points

- `MainActivity` is the only activity and the launcher
  (`android/app/src/main/AndroidManifest.xml:18-28`). `onCreate` reads the
  saved preferences (`android/app/src/main/java/dev/wikiagent/mobile/MainActivity.java:60-63`).
  Pairing arrives through the QR scan, `scan()` (`MainActivity.java:227-236`),
  or the clipboard, `paste()` (`MainActivity.java:238-243`). Both go to
  `connect()`, which saves the origin, clears the cookies and loads the
  pairing link (`MainActivity.java:245-265`).
- `PairingLink` validates the input. `parse` accepts only `https`, a root
  path, no query or user info, and a fragment `pair=<16-128 url-safe chars>`
  (`android/app/src/main/java/dev/wikiagent/mobile/PairingLink.java:10-28`).
  `savedOrigin` re-validates the stored origin (`PairingLink.java:30-42`).
  `sameOrigin` decides which navigation stays inside the app
  (`PairingLink.java:53-62`).
- `ScreenMode` holds the portrait/landscape choice, which sets both the
  activity orientation and the web layout
  (`android/app/src/main/java/dev/wikiagent/mobile/ScreenMode.java:6-21`).
- Unit tests: `android/app/src/test/java/dev/wikiagent/mobile/PairingLinkTest.java`,
  `ScreenModeTest.java`.

## Depends on, depended on by

- It depends on the Google Play services code scanner and JUnit
  (`android/app/build.gradle:47-50`), and on Android Gradle Plugin 8.10.1
  (`android/build.gradle:1-3`).
- It depends on the server's pairing-link format.
  `tool/main/mobile.py:268` builds `{origin}/#pair={code}`, which has to keep
  matching the `SECRET` pattern (`PairingLink.java:10`).
- It depends on the web screen. `publishScreenMode` sets
  `data-native-shell`/`data-native-mode` and fires a `mobile-screen-mode`
  event (`MainActivity.java:136-144`). `web/src/lib/mobile.ts:22-23` reads it.
- It is built by `tool/build_android.ps1`. That script runs
  `gradlew clean testReleaseUnitTest lintRelease assembleRelease bundleRelease`
  (`tool/build_android.ps1:61`) and copies the result to
  `raw/android/wiki-agent.apk` and `.aab` (`tool/build_android.ps1:83-84`).
- It is served by `GET /mobile-install.apk` (`tool/main/app.py:383-389`),
  which reads `mobile.APK = raw/android/wiki-agent.apk`
  (`tool/main/mobile.py:37`).

## What trips a newcomer

- The built APK lives in `raw/android/`, not `artifacts/`. On 2026-10-09 the
  after-merge cleanup of `artifacts/` deleted the served APK, and the install
  QR returned 404 (`docs/mobile.md:133-136`).
- Release signing needs a keystore at `raw/android-signing/release.p12`, or
  wherever `WIKI_ANDROID_KEYSTORE` points, plus `WIKI_ANDROID_KEY_PASSWORD`
  (`android/app/build.gradle:18-27`). The build script creates the keystore
  when it is missing (`tool/build_android.ps1:32-52`). Losing it means phones
  cannot upgrade in place.
- The final gate and CI never build or test Android. Neither
  `verification.toml` nor `.github/workflows/verify.yml` mentions it. Run
  `tool/build_android.ps1` yourself after touching this folder. It needs
  JDK 17+ and SDK platform/build-tools 36 (`docs/mobile.md:125-131`).
- The only native-to-web channel is one-way `evaluateJavascript`
  (`MainActivity.java:139`). Adding a `JavascriptInterface` would break the
  stated contract (`docs/mobile.md:114`).
- The WebView refuses file access, mixed content and third-party cookies,
  and disables its cache (`MainActivity.java:180-196`). A TLS error cancels
  the load instead of prompting (`MainActivity.java:218-222`). A tunnel with
  a bad certificate shows a toast, not the page.
- Layout follows the chosen `ScreenMode`, not the device's rotation
  (`MainActivity.java:147-150`).
- App data is excluded from cloud backup and device transfer
  (`android/app/src/main/res/xml/data_extraction_rules.xml`,
  `AndroidManifest.xml:7-9`). A new phone has to pair again.
- Bump `versionCode` and `versionName` (`android/app/build.gradle:14-15`)
  for each release, or Android will not install the new APK over the old one.
