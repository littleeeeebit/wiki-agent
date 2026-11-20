
The app is published later from a button on the repository screen, as a
signed release build. Add what that button reads to `.wiki/adapter.toml`,
after the tables already there, and change nothing else in that file:

```toml
[release]
version_source = "the one .json or .toml file whose version is the app's"

[release.android]
build = "the command that builds the unsigned release APK"
artifact = "that APK's path from the repository root"
application_id = "{package}"
abi = "the one ABI of the APK's native code, or none when it has none"
```

- `version_source` holds the app's version, `0.1.0` until the first release:
  a `.json` file with a top-level `version`, or a `.toml` file with
  `version` or `[package] version`. When the stack keeps its version
  elsewhere, as Gradle or `pubspec.yaml` do, add one such file, such as
  `version.json`, and have the build read its version from there.
- The release build is given the version in two environment variables:
  `RELEASE_VERSION` (such as `0.1.1`) and `RELEASE_VERSION_CODE` (such as
  `1001`). When they are set, the build uses them as the APK's versionName
  and versionCode. When they are not, it falls back to the version source.
- A Tauri app also gets `RELEASE_TAURI_CONFIG`, the path of a config file
  holding that version and versionCode. Tauri ignores the variables above,
  so `build` hands the Tauri CLI that path as one more `--config` argument,
  read from the variable as the gate's shell reads one.
- That version wiring, and the version file when one is needed, are the only
  code changes this turn may make.
- The release APK is not signed, and not debuggable. The button signs it
  itself.
- `build` follows the gate's rules for a command.

After the gate, the server makes this build once from a clean checkout, with
the version a press would publish after the first one (`0.1.1`, versionCode
`1001`, after `0.1.0`).
It checks the APK's application ID, versionName, versionCode and ABI, and
that it is not debuggable, as a release does. A build that keeps the
source's version instead of the one it is given fails there.
