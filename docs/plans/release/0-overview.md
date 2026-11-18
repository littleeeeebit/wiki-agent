# Release: a merged main becomes a new installable version on GitHub

Purpose. When the person presses the repository's release button, the server
builds the merged `main` into the real app with a new version number and
publishes it as a GitHub Release. The installed app can then update to the
code that review approved. One server step owns this. It works the same for an
app the oneshot made and for any later ordinary task.

## Why this plan exists

realtime-screen-translator showed the gap on 2026-11-18:

- Its only Releases are two Android test builds, `android-test-6f877a6` and
  `android-test-1721b04`. The repair agent made them by hand in the middle of
  the review of PR #1.
- The merged `main` (`1dfdbb0`, after #2) has no build. No Windows installer
  was ever published.
- `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml` and `package.json` all
  still say `0.1.0`. Nothing tells two builds apart. Android also refuses to
  install a build over one with the same `versionCode`.
- The oneshot's S8 (`oneshot_pr.released`) builds a debug APK from the head
  before review and attaches it to a draft prerelease for the first pull
  request. Review repairs never reach that APK. After the merge, nothing
  builds again.

No step owns the path from fixed code, to a new version number, to the
installable app, to GitHub. The oneshot cannot own it: it ends when it
publishes its pull request, and every later change arrives as an ordinary task.

## Decisions

These are the person's, made on 2026-11-18:

- Releasing is outward-facing, so it starts only from a button on the
  repository screen, never automatically after a merge. The button shows the
  last released version and how many commits `main` is ahead of its tag. The
  person picks `patch`, `minor` or `major` there.
- Once pressed, the release runs without stopping for the person. Progress is
  shown on the screen, and a failure goes to an agent turn to fix (flow step 6).
  It never ends in a prompt asking the person to type something.

These are proposals that each step's document settles:

- The version comes from the tag at build time, for example
  `tauri build --config '{"version":"0.1.1"}'`. It is not a commit that edits
  version files, because such a commit would need its own pull request and
  review. The source keeps its old number, and the tag and the built app
  carry the real one.
- The server builds on this machine, where the Android SDK and the oneshot
  toolchain already are, rather than in the target repository's GitHub
  Actions. Actions would need the signing key stored as a GitHub secret and
  Windows runner minutes on a private repository. This decision can be
  reversed per repository later.

## Where it lives

| Part | Place |
| --- | --- |
| The release step | New module `tool/main/release.py`. The GitHub Release create/upload logic moves here from `oneshot_pr.released`, and S8 calls it. |
| Per-repository settings | A `[release]` table in `.wiki/adapter.toml`: one entry per kind (`android`, `desktop`) with its build command, artifact path and version mechanism. The oneshot writes it, as S4 writes `gate_cmd`. |
| The record | A per-repository release ledger: tag, commit, each artifact's name, `sha256` and URL, and the reason for a failure. It belongs to the repository, not to a task spec. |
| Screen | A release card on the repository screen: last tag, commits ahead, the button, and the running state |
| Signing | A release keystore outside the repository, named by path in the machine's settings, never in the adapter |

## The flow

```text
[release] pressed (patch | minor | major)
  -> 1. version: last tag v0.1.0 -> v0.1.1
  -> 2. clean checkout of origin/main at its current commit
  -> 3. build each declared kind with the version injected
  -> 4. verify: version inside each artifact == tag, signature == last release's, sha256
  -> 5. GitHub Release v0.1.1 with the artifacts, notes from merged PR titles since v0.1.0
  -> 6. on failure: an agent turn fixes the build on a branch -> ordinary PR -> review -> merge -> press again
```

Step 4 is what makes the release worth trusting. A build whose `versionName`
or `versionCode` (read with `aapt dump badging`), or whose Windows file
version, differs from the tag is refused. So is an APK signed with a different
key than the last release. Today's APKs carry the debug signature of whichever
machine built them, and a phone refuses an update signed by a different key.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Release step for Android | `release.py`, the adapter `[release]` table, version from the tag, clean checkout, APK verification, the GitHub Release, the ledger; S8 calls the shared upload | Not started |
| 2 | Signing | A release keystore outside the repository; refuse an APK whose signature differs from the last release's | Not started |
| 3 | Button and card | The repository screen's release card, with the bump choice and live state | Not started |
| 4 | Windows installer | The `desktop` kind's NSIS build and its file-version check | Not started |
| 5 | Failure to agent | A failed build or check becomes a bounded agent turn on a branch; its fix goes through the ordinary review loop | Not started |
| 6 | Oneshot writes `[release]` | The oneshot fills the table for the kinds it builds, so its first app can be released from day one | Not started |

## What this does not do

- It does not publish to a store (Play Store, Microsoft Store), and it does
  not push an update to installed apps. An in-app update check against the
  latest Release is a separate plan.
- It does not run on a merge by itself.
- It does not bump version files in the source.
