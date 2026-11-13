# Windows desktop release

This procedure builds the Windows x64 setup executable. It does not publish a
release, start review, or authorize metered acceptance. The normal development
launcher remains `tool/app.cmd`; packaged builds never use its checkout path.

## Build inputs

Use Windows with Python, Node, Rust and the Visual C++ build tools. Install
`requirements-dev.txt` and run `npm --prefix web ci`. The locked Tauri CLI is a
development dependency. Python 3.14.10's embeddable x64 archive is downloaded
from Python.org and checked against the digest pinned in `tool/build_desktop.py`.
Backend dependencies are vendored as Windows wheels. The runtime's isolated
`._pth` excludes the developer's Python installation and user site packages.

Only tracked runtime code, shared rules, skills, documentation, the fitted
policy, and the built frontend enter the payload. Personal adapters, `.env`,
`.wiki`, `raw/`, tests and developer environments are excluded. A payload hash
inventory accompanies each build. Build a publishable installer from a clean
commit; `--allow-dirty` exists solely for local development artifacts.

## Update key

Tauri's update key authenticates downloaded releases. It does not buy or supply
a Windows code-signing certificate. Keep the private key outside Git and back
it up securely before distributing any installer: later updates must use the
same key. The compiled installer contains only its public key.

Create an update key once with the locked CLI's `tauri signer generate --ci
--write-keys <ignored-private-key-path>` command. It can print key material;
keep its output local and do not paste it into task logs or issues. Use the
generated `.pub` file as the build's public key. Set `TAURI_SIGNING_PRIVATE_KEY`
to the private-key file path and, for a protected key,
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` in the build process environment.

```powershell
python tool/build_desktop.py --version 0.1.0 --updater-public-key "C:/private/wiki-agent-update.key.pub"
```

Each version gets a new directory under `raw/desktop/releases/<version>/`.
An incomplete build remains there for diagnosis; use a new version for another
attempt. Successful output includes the NSIS `*-setup.exe`, `.exe.sig`,
`latest.json`, the staged payload and `payload-sha256.json`. The setup installs
for the current user and bootstraps WebView2 when needed. App dependencies are
bundled; external agent tools are prepared through onboarding.

The default program directory is `%LOCALAPPDATA%\wiki-agent`; all writable
application data, including fallback fd/jq downloads, belongs to its sibling
`%LOCALAPPDATA%\wiki-agent-data`. Do not select the data directory as a custom
installation directory. Uninstall and update acceptance must use the default
data path, without a `WIKI_DATA_HOME` override that could hide a collision.

The package build uses `raw/desktop/target`, separate from the running source
window's Cargo target. Its relocated-image check can be run with
`python web/tests/packaged_desktop.py --image raw/desktop/releases/<version> --out raw/desktop/acceptance/<version>`.
It copies the executable and payload to a temporary path with spaces and Korean
characters, removes developer tools from PATH, uses a disposable Windows user
home with the real default data-folder rule and disposable login
homes, and exercises real backend requests, terminal commands and two windows.
This is development-host evidence, separate from installing and uninstalling
on the owner's isolated Windows environment. `onboarding_browser.py` checks
screen wiring and layout against synthetic responses; it does not install tools
or log in to providers.

## Publication and automatic updates

After release authorization, publish the setup, its signature and `latest.json`
together under the matching `v<version>` GitHub release. The configured feed is
`https://github.com/littleeeeebit/wiki-agent/releases/latest/download/latest.json`.
The manifest's platform key is `windows-x86_64`; its asset URL and signature
must name the exact installer from this build. Keep the update private key
separate from the release assets. Never turn off signature verification to
repair a broken release.

Tauri checks the feed in the installed window. Before installing, it checks the
offered version again and downloads and verifies the payload. Only its
pre-install exit callback closes terminals and the backend. A download or
signature failure keeps the running app open. If launching the Windows installer
fails after shutdown, the shell attempts to restore the backend on the same port.
If Windows installation itself fails after process exit, reopen the app or
run a verified setup; user records remain outside the installer-owned directory.

## Required acceptance

The owner chose an owner-run checklist and owner-run live hook probes. Use an
owner-provided disposable Windows environment with no app checkout,
Python, Node, Rust or app build tools. Start with no selected coding agent and
install its prerequisites through onboarding. The owner completes official
interactive login and runs the quota-consuming hook probes. These choices do
not authorize the implementation agent to start metered sessions.

1. Record the setup version, SHA-256, Windows version, clean initial tool state,
   and the disposable user profile. Install and launch from the Start menu.
2. Exercise prerequisite success, failure, cancellation and retry. Verify the
   displayed executable paths and versions after installation, not just exit codes.
3. Complete official login, select a workspace with spaces and Korean characters,
   and connect a disposable Git project. Capture actual desktop interactions and
   backend requests. Confirm real SessionStart and other supported hook events
   from new Claude and Codex sessions; direct script invocation is separate proof.
4. Open two windows on separate projects, exercise terminals, restart, and close
   one then the last window. Verify project isolation, one-server ownership and
   shutdown of backend and child processes.
5. Produce records, restart and confirm persistence. Import a stopped source
   fixture and compare backup hashes and original bytes; test interrupted import.
6. Upgrade from one signed-update payload to another version. Test an unavailable
   download and a wrong signature, then retry a valid update. Do not publish test
   update metadata to the production feed.
7. Uninstall through Windows and verify that conversations, task state, settings,
   project `.wiki/` and CLI credentials remain. Reinstall and confirm the records.

Keep receipts and screenshots in ignored `raw/desktop/acceptance/`. Record the
exact commit and installer hash with every result. Unit and fixture-browser
checks do not establish this isolated desktop acceptance.

### Isolated HTTPS update feed

For a browser-only hosting procedure, starter files and the ordered failure
cases, follow [Hosting the Windows update test feed](windows-update-test-feed.md).

The owner selected a separate HTTPS feed for update acceptance. Supply its
absolute `latest.json` URL before building the two test versions. The builder's
`--update-feed <https-url>` embeds that endpoint and makes the generated
manifest reference the setup beside that JSON file. It rejects HTTP, credentials,
query parameters and fragments. Production builds omit this argument and keep
the GitHub release feed above. Test-feed builds must never be distributed as
production installers.

Build two increasing numeric versions from the same candidate commit and with
the same update key. Install the older test build, then place the newer build's
`latest.json` and matching setup on the isolated HTTPS host. Keep the host's
normal trusted certificate checks enabled. The signature is already in the
manifest; retain the `.exe.sig` beside the build for audit. Hosting or changing
remote assets requires the owner's publication authorization.

For unavailable-download and signature-failure cases, the owner changes only
the isolated feed: first point its asset URL at a missing file, then serve an
invalid signature for the real setup. Record each displayed error and continued
backend availability. Restore the exact generated manifest and perform the
successful retry. Confirm the new version, original records and CLI login after
restart. Keep the two installer hashes, feed URL and failure/success evidence
with the Windows checklist. An expected error alone does not pass the upgrade.
