# Release: a merged main becomes a new installable version on GitHub

Purpose. When the person presses the repository's release button, the server
builds one pinned commit of `main` into the real app with a new version number,
signs it with the repository's release key and publishes it as a GitHub
Release. The installed app can then update to the code that review approved.
One server step owns this. It works the same for an app the oneshot made and
for any later ordinary task.

## Why this plan exists

realtime-screen-translator showed the gap on 2026-11-18:

- Its only Releases are two Android test prereleases, `android-test-6f877a6`
  and `android-test-1721b04`. Each holds one debug APK, built before PR #1
  merged.
- No installable release was published after a merge. CI did run on both
  merge commits, but it builds the frontend, not an app. No Windows installer
  was ever published.
- `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml` and `package.json` all
  still say `0.1.0`, so nothing tells two builds apart.
- The oneshot's S8 (`oneshot_pr.released`) freezes a debug APK from the head
  its final gate passed, before review, and attaches it to a draft prerelease
  that the first pull request links. Review repairs never reach that APK.
  After the merge, nothing builds again.

No step owns the path from fixed code, to a new version number, to the
installable app, to GitHub. The oneshot cannot own it: it ends when it
publishes its pull request, and every later change arrives as an ordinary task.

## Decisions

These are the person's, made on 2026-11-18:

- Releasing is outward-facing, so it starts only from a button on the
  repository screen, never automatically after a merge. The button shows the
  last released version and how many commits `main` is ahead of it. The person
  picks `patch`, `minor` or `major` there.
- One press authorizes one attempt: publishing one commit, pinned when the
  attempt starts, under one version. Within that attempt nothing stops for the
  person. Progress is shown on the screen, a transient failure is retried, and
  a build failure goes to an agent turn (see "When an attempt fails"). It never
  ends in a prompt asking the person to type something.

These are proposals that each step's document settles:

- The version comes from the tag at build time, through Tauri's config
  overlay, for example `tauri build --config '{"version":"0.1.1"}'`. It is not
  a commit that edits version files, which would need its own pull request and
  review. The source keeps its old number. The tag and the built app carry the
  real one. On Android the overlay reaches Gradle through the
  `tauri.properties` file the Tauri CLI writes. On Windows `tauri-build` writes
  it into the app's EXE resource and the NSIS installer's version.
- The server builds on this machine, where the Android SDK and the oneshot
  toolchain already are, rather than in the target repository's GitHub
  Actions. Actions would need the signing key stored as a GitHub secret and
  Windows runner minutes on a private repository. This can be reversed per
  repository later.

## Where it lives

| Part | Place |
| --- | --- |
| The release step | New module `tool/main/release.py`, the only caller that publishes production Releases |
| GitHub transfer | A low-level helper both callers share: create, view and upload with an explicit repository and target commit. Each caller keeps its own policy. S8 keeps its head-named draft prerelease, digest reuse and `--clobber`. A production release never replaces a published asset. |
| Per-repository settings | An optional `[release]` table in `.wiki/adapter.toml`, read from the pinned commit through a typed reader. Per kind (`android`, `desktop`) it holds the build command, the artifact path, the application ID and the expected ABI. The current writer (`connect.adapter_text`) emits only `agents` and `[slots]`. Its writers must keep tables they do not own. |
| The checkout | A fresh detached checkout per attempt, in a release-owned directory outside the task worktree allocator, removed only by its own cleanup. The detached survey checkout (`refactor_survey`) is the precedent. |
| The ledger | Server state under `raw/releases/`, keyed by the canonical remote (`github.com/<owner>/<repo>`). Two windows or two checkouts of one repository share one reservation and one recovery record. |
| Signing | A release keystore per repository outside every checkout, with its credentials given only to the signing step. The adapter's build command and the repair agent never see them. |
| Screen | A release card on the repository screen: last production version, commits ahead, the button and the running attempt |

## One attempt

```text
[release] pressed (patch | minor | major)
  -> reserve: one attempt per remote; a second press while one runs is refused
  -> pin: fetch, take origin/main's SHA once; the version is chosen against it
  -> record pending in the ledger before any remote write
  -> checkout: fresh detached checkout of the SHA
  -> build: each declared kind, version injected; unsigned release artifact
  -> sign: the repository's release key, outside the build command
  -> verify: every check below on the final signed bytes
  -> publish: draft Release, tag targeting the SHA, assets uploaded,
              tag and asset digests reconciled, then made public
  -> ledger: published, with SHA, tag, each asset's name, sha256 and URL
```

The version is the last eligible production version bumped by the person's
choice. Eligible means a published, non-draft, non-prerelease Release whose tag
is `v<major>.<minor>.<patch>`. Test tags such as `android-test-*` and S8's
`oneshot-apk-*` never count. When there is none, the first release uses the
version in the source (`0.1.0` for the translator), and its notes say it is the
first.

The notes list the titles of the pull requests merged between the previous
production tag and the pinned SHA, bounded by that SHA and not by whatever
`main` has become since.

### Checks before publishing

Each artifact is refused unless all of these hold:

| Check | Android | Windows (step 6) |
| --- | --- | --- |
| Version | `versionName` equals the tag (`aapt dump badging`) | EXE resource and installer `FileVersion` equal the tag, with `0.1.1.0` normalized to `0.1.1` |
| Monotonic | `versionCode` strictly greater than the previous production APK's (Tauri's default maps `0.1.1` to `1001`; step 1 fixes the mapping and its bounds) | Version greater than the previous production installer's |
| Identity | Application ID and ABI equal the adapter's; a release build, not debuggable | Product name and publisher equal the previous release's |
| Signature | `apksigner verify --print-certs` matches the repository's expected certificate | none until code signing is planned |
| Digest | `sha256` of the final signed bytes, the same bytes uploaded | the same |

Android rejects an update with a lower `versionCode`, or one signed by a
different certificate. A strictly increasing code and one durable certificate
are what keep installed copies updatable.

### Restart and collisions

The ledger's pending record lets a restarted server finish or end the attempt
it was in. That covers a draft that exists, a partial upload and a publish
whose answer was lost. The server reconciles before it marks anything
published. The tag must point at the pinned SHA, and each asset's digest must
equal the ledger's. A remote tag or asset that differs, by commit or by
digest, ends the attempt as failed. It never replaces the existing one.

### When an attempt fails

- A transient failure, such as a network error or a GitHub 5xx, retries the
  same pinned attempt, a bounded number of times.
- A build or check failure that needs a source change goes to a bounded agent
  turn. The agent works on its own branch and opens an ordinary pull request
  under the ordinary review loop. The attempt then ends as failed, and the card
  links that pull request. The fix's merge needs its own authorization, as
  every merge does. Publishing the merged fix is a new release press, because
  one press authorizes only the commit it pinned.

## Existing debug installations

Phones that installed the translator's debug APKs carry a debug certificate.
The first production APK, signed with the release key under the same
application ID, cannot update them: the user uninstalls once and installs the
release. Step 2 writes that into the first release's notes. The alternative,
a distinct application ID for releases, keeps both apps side by side. That
choice is left to step 2's document.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Android build and verify, unpublished | The typed `[release]` reader, version choice and pinning, the release-owned detached checkout, the release build with the injected version, every Android check on a disposable target over two consecutive versions; nothing is published | Not started |
| 2 | Signing | The per-repository release keystore and expected certificate, set up once; signing outside the build command; the debug-installation note | Not started |
| 3 | Transfer and ledger | The shared GitHub transfer helper, which S8 moves onto with its own policy unchanged; the `raw/releases/` ledger, the reservation, explicit targets, reconciliation and restart | Not started |
| 4 | Button and card | The repository card and its button, the one entry point that publishes; requires steps 2 and 3 | Not started |
| 5 | Failure to agent | Bounded retries and the agent turn whose fix goes through the ordinary review loop | Not started |
| 6 | Windows installer | The `desktop` kind's NSIS build and its checks | Not started |
| 7 | Oneshot writes `[release]` | The oneshot fills the table for the kinds it builds, keeping every other adapter table | Not started |

## What this does not do

- It does not publish to a store (Play Store, Microsoft Store), and it does
  not push an update to installed apps. An in-app update check against the
  latest Release is a separate plan.
- It does not run on a merge by itself, and one press never publishes a commit
  it did not pin.
- It does not bump version files in the source.
