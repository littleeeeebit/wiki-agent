
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

- `version_source` holds `0.1.0` now: a `.json` file with a top-level
  `version`, or a `.toml` file with `version` or `[package] version`.
- The release build is given the version in two environment variables:
  `RELEASE_VERSION` (such as `0.1.1`) and `RELEASE_VERSION_CODE` (such as
  `1001`). When they are set, the build uses them as the APK's versionName
  and versionCode. When they are not, it falls back to the version source.
  Wiring that into the build file is the one code change this turn may make.
- The release APK is not signed, and not debuggable. The button signs it
  itself.
- `build` follows the gate's rules for a command.

After the gate, the server makes this build once from a clean checkout and
checks the APK's application ID, versionName, versionCode, ABI and that it is
not debuggable, as a release does.
