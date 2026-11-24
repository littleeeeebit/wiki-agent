
The PC app's installer is the one its release build makes. When the PC app is
a Tauri app, {where}, changing nothing else in that file:

```toml
{head}[release.desktop]
build = "the command that builds the release build's NSIS installer"
artifact = "that installer's path from the repository root, {version} standing for the version"
exe = "the app EXE that build makes, its path from the repository root"
product_name = "the product name the installer registers with Windows"
publisher = "the publisher the app EXE names"
```
{source}
- The release build is given the version in `RELEASE_VERSION` (such as
  `0.1.1`) and `RELEASE_TAURI_CONFIG`, the path of a config file holding it.
  Tauri ignores the variable, so `build` hands the Tauri CLI that path as one
  more `--config` argument, read from the variable as the gate's shell reads
  one, and builds the NSIS bundle alone (`--bundles nsis`).
- The frontend build named the installer `{installer}`; `artifact` is where
  the release build leaves it, the version in its name written `{version}`.
- That version wiring is the only code change this table may bring.
- The installer is not signed.
- `build` follows the gate's rules for a command.

When the PC app is not a Tauri app, write no `[release.desktop]`: this version
attaches no installer for it, and the pull request says why.

After the gate, the server makes this build once from a clean checkout, with
the version a press would publish after the first one (`0.1.1` after
`0.1.0`). It checks the installer's version, product name and publisher, that
it holds the very app EXE the build made, and what it registers with Windows,
as a release does. The installer that passes is the one the pull request
attaches.
