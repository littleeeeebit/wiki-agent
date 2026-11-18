# Release: a merged main becomes a new installable version on GitHub

Purpose. When the person presses the repository's release button, the server
builds one pinned commit of `main` into the real app with a new version number,
signs an Android APK with the repository's release key and publishes the
artifacts as a GitHub Release. The installed app can then update to the code that review approved.
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
- The oneshot's S8 copies the debug APK its final gate built, before review,
  with its digest (`oneshot_pr.froze`). It then verifies that copy and attaches
  it to a draft prerelease that the first pull request links
  (`oneshot_pr.released`). Review repairs never reach that APK, and no later
  step publishes an installable release.

No step owns the path from fixed code, to a new version number, to the
installable app, to GitHub. The oneshot cannot own it: it ends when it
publishes its pull request, and every later change arrives as an ordinary task.

## Decisions

These are the person's, made on 2026-11-18:

- Releasing is outward-facing, so it starts only from a button on the
  repository screen, never automatically after a merge. The button shows the
  last released version and how many commits `main` is ahead of it. The person
  picks `patch`, `minor` or `major` there. Before the first production release
  of a repository there is nothing to bump: the card shows the first version
  the press would publish instead (see "One attempt") and offers no choice.
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
| GitHub transfer | `tool/main/github_release.py`, a low-level helper both callers share. It runs `gh api` against the REST API, and every call names its repository as `owner/name`, so nothing reads a working directory's remote. A failure carries its HTTP status and says whether it is transient: no answer, 429, 5xx, or a 403 rate limit. Each caller keeps its own policy. S8 keeps its head-named draft prerelease on the default branch, its digest reuse and its replacement of the asset of the same name. A production release never replaces a complete asset. |
| Per-repository settings | An optional `[release]` table in `.wiki/adapter.toml`, read from the pinned commit through a typed reader. It names the one version-source file for the seed. Per kind (`android`, `desktop`) it holds the build command, the artifact path, the application ID and the expected ABI. The build command gets the version as `RELEASE_VERSION` and `RELEASE_VERSION_CODE`. A Tauri build passes `RELEASE_TAURI_CONFIG`, an overlay file beside the checkout, as one more `-c`. `TAURI_CONFIG` cannot carry the version, because the CLI replaces it with its own `-c` values. Step 1's rehearsal found this: it built the same bytes twice. The current writer (`connect.adapter_text`) emits only `agents` and `[slots]`. Its writers must keep tables they do not own. |
| The checkout | A fresh detached checkout per attempt, in a release-owned directory outside the task worktree allocator, removed only by its own cleanup. The detached survey checkout (`refactor_survey`) is the precedent. |
| The ledger | Server state under `raw/releases/`, keyed by the canonical remote (`github.com/<owner>/<repo>`). Two windows or two checkouts of one repository share one reservation and one recovery record. `tool/main/release_publish.py` keeps it in `ledger.json` beside a lock. Each write replaces the whole file through a fresh staging file. A press records how many attempts the ledger held when it chose its version. If another attempt was added since, the reservation refuses the press. The live worker holds an OS lock on its attempt from before the reservation to its end, and a `finish` that finds the lock held leaves the attempt to that worker. A transient GitHub failure leaves the attempt pending, and so does a ledger write that fails after a remote write. `recover` carries every pending attempt on through the restart table, and the server calls it at start once step 5 wires the button. |
| Signing (Android) | A release keystore per repository at `%LOCALAPPDATA%\wiki-agent-data\release-keys\<canonical remote>\release.jks`, outside every checkout. That location holds even unpackaged, because there the data root is the checkout. The password is random and lives only in this Windows account's Credential Manager (`wiki-agent release key <remote>`). It is given only to the key tools, through their environment, and is never handed to the adapter's build command or the repair agent. Those run as the same Windows account, and that account can read its own vault and profile, so the real boundary is the account. Before step 5 enables publishing, build or repair code that is not trusted must run under another account. One lock per repository serializes key creation across processes. Every check is made again under it. `certificate.sha256` beside the keystore is written last, by an atomic rename, and marks the key as established. After that, a lost keystore, a lost password or a changed certificate is refused, never replaced. A creation that stopped is finished only when the stored password opens the key it left. A keystore that no password opens is left for a person, staged or final. So is a surviving password with no keystore. Nothing deletes or overwrites either. The person backs up the keystore and the credential together. |
| Screen | A release card on the repository screen: last production version, commits ahead, the button and the running attempt |

## One attempt

```text
[release] pressed (patch | minor | major, or the seed for a first release), carrying the shown version
  -> check: an attempt already running for this remote refuses the press
  -> pin: fetch, take origin/main's SHA once; the selector recomputes the version
  -> validate: a version other than the shown one refuses the press; nothing is written
  -> reserve: atomically take this remote's one attempt (a press that loses the race is
             refused), recorded pending in the ledger before any remote write
  -> checkout: fresh detached checkout of the SHA
  -> build: each declared kind, version injected; Android gives an unsigned release APK
  -> sign: Android only, the repository's release key, outside the build command
  -> verify: every check below on the final bytes
  -> manifest: each asset's name, size and sha256 in the ledger; the exact files
               kept in the attempt's directory until it ends
  -> tag: push the tag at the SHA, then read it back from the remote
  -> publish: draft Release on that existing tag, its ID recorded; assets uploaded,
              reconciled against the manifest, then made public
  -> ledger: published, with SHA, tag, release ID, each asset's name, sha256 and URL
```

The version is the last eligible production version bumped by the person's
choice. Eligible means a published, non-draft, non-prerelease Release whose tag
is `v<major>.<minor>.<patch>`. Test tags such as `android-test-*` and S8's
`oneshot-apk-*` never count. When there is none, the first release uses the
seed version, read from the one file the adapter's `[release]` table names as
the version source (`src-tauri/tauri.conf.json` for the translator, `0.1.0`).
Its notes say it is the first.

A version is taken when its tag exists on the remote for any reason, for
example a failed attempt's tag or a tag someone else pushed. Version choice
skips a taken version and moves to the next patch, both after a bump and from
the seed. One selector computes the version, used both for the card and for
the attempt. The card shows exactly the version a press would publish, and
the press carries it. If the selector gives a different version when the
attempt pins its SHA, because a tag appeared in between, the press is refused
before anything starts. The card then shows the new version, and nothing has
been reserved or written.

The notes list the titles of the pull requests merged between the previous
production tag and the pinned SHA, bounded by that SHA and not by whatever
`main` has become since.

### Checks before publishing

Each artifact is refused unless all of these hold. "The version" is the tag's
SemVer value without its `v`: tag `v0.1.1` means `0.1.1`.

| Check | Android | Windows (step 6) |
| --- | --- | --- |
| Version | `versionName` equals the version (`aapt dump badging`) | EXE resource and installer `FileVersion` equal the version, with `0.1.1.0` normalized to `0.1.1` |
| Monotonic | `versionCode` strictly greater than the previous production APK's and equal to the version's code: Tauri's mapping, major × 1000000 + minor × 1000 + patch (`0.1.1` is `1001`), with minor and patch at most 999 and major at most 2099, so the code stays under Google Play's 2100000000. The step injects it with the version. | Version greater than the previous production installer's |
| Identity | Application ID and ABI equal the adapter's and the previous production APK's of this kind; a release build, not debuggable | Product name and publisher equal the adapter's and the previous production installer's |
| Signature | `apksigner verify --print-certs` matches the repository's expected certificate and the previous production APK's | none until code signing is planned |
| Digest | `sha256` of the final bytes, the same bytes uploaded | the same |

Identity is checked against the previous production artifact of the same
kind, not only against the adapter at the pinned commit. A merge that changes
the Gradle application ID and the adapter together would otherwise pass, and
Android would install the result as a separate app. The first production
release of a kind has no previous artifact, so it is checked against the
configured identity and expected certificate. Those become the baseline.

Android rejects an update with a lower `versionCode`, or one signed by a
different certificate. A strictly increasing code and one durable certificate
are what keep installed copies updatable.

### Restart and collisions

Every remote write happens after the ledger already holds what it should
produce: the manifest before the tag, and the tag and SHA before the draft.
The release ID is recorded as soon as the draft exists. A restarted server
reads the ledger and finishes or ends the attempt it was in:

| State found | What the server does |
| --- | --- |
| Tag missing | Pushes it at the SHA |
| Tag at the SHA | Goes on. GitHub ignores a draft's `target_commitish` when the tag already exists, so only this read proves the target |
| Tag at another commit | Ends the attempt as failed |
| No release ID, draft creation answered nothing | Finds the draft by tag name and the attempt ID written in its body. None found means it creates one |
| An asset in `starter` state (an upload GitHub cut off, for example with a 502) | Owned by this draft and incomplete, so it deletes it and uploads again, a bounded number of times |
| A complete asset whose digest equals the manifest's | Keeps it |
| A complete asset whose digest differs | Ends the attempt as failed. A complete asset is never replaced. |
| Everything matches | Makes the draft public and records the attempt published |
| The recorded release is already public (`draft` false, `published_at` set): the publish was accepted but its answer or the ledger write was lost | Read-only. If the tag is at the SHA and the asset set and digests equal the manifest exactly, it records the attempt published. Otherwise it ends the attempt as failed and leaves the published release as it is. |

Before publishing, the server reads the draft one last time. The draft's own
tag name, the tag's commit and the asset set must all equal the attempt's.
GitHub has no conditional publish, though. A maintainer who changes the draft
or moves the tag between that read and the publish request still wins. The
read after publishing then records the attempt as failed and leaves the release
as it is. A tag ruleset on `v*` closes the tag side of this when it is
configured correctly. The draft side stays open to anyone who can edit the
repository's releases. Closing it means limiting who can edit them. Immutable
releases protect a release only once it is published.

The server never deletes a remote tag or release. An attempt that ends without
publishing leaves its draft and its tag where they are, and the ledger records
them as that attempt's. Ownership cannot be proven safely enough to delete.
Another maintainer can push the same tag at the same commit, which a push
reports as up to date. Another writer can also publish the draft between the
server's read and its delete, and GitHub's delete takes no draft-only
condition. The next press is not blocked, because version choice skips any
version that is taken (see "One attempt").

### When an attempt fails

- A transient failure, such as a network error or a GitHub 5xx, retries the
  same pinned attempt, a bounded number of times. The press tries each
  GitHub read before the manifest and the whole restart table three times,
  waiting 15 and then 60 seconds (`release_publish.WAITS`). The reads are the
  release and tag lists, the previous APK's download and the previous tag.
  A person's stop ends the waiting. After the last try of the restart table,
  the attempt stays pending for `recover`. Before the manifest nothing remote
  has been written and no version is taken, so a failure that outlasts its
  tries ends the attempt as failed. The next press builds the same version
  again.
- A build or check failure that needs a source change goes to a bounded agent
  turn. The agent works on its own branch and opens an ordinary pull request
  under the ordinary review loop. The attempt then ends as failed, and the card
  links that pull request. The fix's merge needs its own authorization, as
  every merge does. Publishing the merged fix is a new release press, because
  one press authorizes only the commit it pinned.

What needs a source change is narrow:

- The build command exits with an error, makes no artifact, or makes one
  `aapt2` cannot read as an APK (`release.BuildFailed`). The check happens
  before signing, so a broken product is the build's failure.
- The built APK's versionName, versionCode, application ID or ABI is wrong,
  or the APK is debuggable (`release.built_wrong`).

A wrong certificate is the release key's problem (`release.signed_wrong`).
So are a missing release tool, a key the server refuses, and a git or GitHub
failure. A build that a person stopped, or that hit its deadline or went
silent, is the machine's. So is one whose shell reports, in the log's last
lines, that it cannot find a program by bare name that is not on the build's
`PATH` (`release.lacks_tool`). The message is the shell's own: the English
ones, and what this machine's shells answer, in their language, when the
server asks them once through the gate for a program that exists nowhere
(`release.learned_shapes`). A source error that merely quotes a name, such
as `'theme' is not exported` or its Korean counterpart, is not one. A missing
script given by path is the
repository's, and a source change can restore it. None of the machine's
failures goes to an agent.

`release_repair.start` makes the turn an ordinary task. Its spec,
`release-fix-<version>`, carries the attempt, the tag, the pinned commit and
the failure with the build log's last 40 lines. Secrets are redacted from
the log (`errorlog.redact`). The build ran without GitHub tokens or the key
password in its environment (`release.UNSHARED`). No agent session inherits
the key password (`host.environment`). The repair's session, whenever it is
made, also starts without the server's GitHub token variables
(`host.TOKENS`); the server publishes its pull request. The spec names
version files, signing and publishing as out of bounds.

The task gets a linked worktree on a new branch at the pinned commit. The
person's checkout is never switched, and nothing in it is carried over.
The gate read from that worktree is the done condition, so the person's
branch never supplies it. Its path is held before the worktree is made, so
a path another request holds is neither made nor removed. If the spec
cannot be saved, the worktree and its branch are removed while still held,
and the hold is released even if that removal fails. The branch is kept if
another checkout stands on it. Anything left, including a folder a process
still holds open, is named in the recorded failure. A
project chosen as a linked checkout gets no task, since task worktrees are
made beside the original checkout only. Its pull request goes to the branch
the attempt pinned (`main`). Its first turn starts as `[시작]` starts one.

The agent never sees the key or its password. A GitHub login the account
holds stays readable to that account, which is why "Signing (Android)"
requires another account before step 5 enables publishing. A repository the
server does not know as a project gets no task: the turn's end finds the
task's project by name, so such a task would never leave `작업 중`. The
ledger records the task's ID under the attempt's `repair`. When its first
turn cannot start, the task waits in its worktree, and the ledger records
both its ID and the refusal. `release.rehearse` hands its failures over the
same way, so the unpublished path exercises the hand-off. With a repair,
it rehearses only a branch that `origin` has, since that branch is where
the repair's pull request goes.

## Existing debug installations

Phones that installed the translator's debug APKs carry a debug certificate.
The first production APK, signed with the release key under the same
application ID, cannot update them: the user uninstalls once and installs the
release, which loses the debug app's data. The application ID stays as it
is. The first release's notes say this (`release.FIRST_RELEASE_NOTE`), and
step 3 puts that note in the Release.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Android build and verify, unpublished | The typed `[release]` reader, the seed and the one version selector for card and attempt (skipping a taken version, including a failed first release's seed; refusing a press whose version changed by pin time), pinning, the release-owned detached checkout, the release build with the injected version. A disposable test key and its certificate sign the step's APKs, so every Android check runs over two consecutive versions on a disposable target, including the refusal of a changed application ID and of a changed certificate. Nothing is published, and the test key never signs a production artifact. | Done: `tool/main/release.py`. The rehearsal on a disposable clone of the translator built 0.1.0 (versionCode 1000) and 0.1.1 (1001) from one SHA. It refused a second key and a moved application ID on the real bytes. |
| 2 | Signing | The per-repository release keystore and expected certificate, set up once; signing outside the build command; the debug-installation note. Required before anything is published. | Done: `release.release_key` and the `key` command in `tool/main/release.py`. A throwaway remote's key was made in Credential Manager. An APK signed with it verified against the recorded certificate, and the key and credential were then removed. No real repository has a key yet; the first is made once, on request. |
| 3 | Transfer and ledger | The shared GitHub transfer helper, which S8 moves onto with its own policy unchanged; the `raw/releases/` ledger, the reservation, the manifest, the tag read-back, the draft ID, every row of the restart table including a lost publish answer, and a failed attempt's draft and tag left in place and recorded | Done: `tool/main/github_release.py` and `tool/main/release_publish.py`. A clone of the translator, pushed as `main` of the private repository `LittleBitAI/wiki-agent-release-rehearsal`, was published twice from one SHA. The first press made v0.1.0 (versionCode 1000) with the first-release note and created that remote's key. The patch press made v0.1.1 (1001). It was checked against v0.1.0's APK downloaded from the Release. Each tag reads back at the pinned SHA, and each asset's GitHub digest equals its manifest entry. The rehearsal key and credential were removed afterwards, so that repository takes no further releases. The restart rows run over a fake GitHub in `tool/test_release_publish.py`. |
| 4 | Failure to agent | Bounded retries and the agent turn whose fix goes through the ordinary review loop, exercised on the unpublished path | Done: `release_publish.WAITS` and `_tried`, `release.BuildFailed` with `built_wrong` and `signed_wrong`, and `tool/main/release_repair.py`. The live check committed an import of a missing module to `main` of `LittleBitAI/wiki-agent-release-rehearsal`. The rehearsal's build failed on it and made the task `release-fix-0-1-2`. Its agent turn removed the import, passed the repository's gate (165 Rust and 30 frontend tests), and the round check opened that repository's PR #1. The PR was closed unmerged. Two earlier runs found that a repository the server does not know never finishes its task, now refused, and that the translator tracks `.wiki/.sync`, which the wiki hook rewrites every turn. |
| 5 | Button and card | The repository card and its button, the one entry point that publishes; the seed or the bump choice. Its publishing action is enabled only with steps 2, 3 and 4 in place. | Not started |
| 6 | Windows installer | The `desktop` kind's NSIS build and its checks | Not started |
| 7 | Oneshot writes `[release]` | The oneshot fills the table for the kinds it builds, keeping every other adapter table | Not started |

## What this does not do

- It does not publish to a store (Play Store, Microsoft Store), and it does
  not push an update to installed apps. An in-app update check against the
  latest Release is a separate plan.
- It does not run on a merge by itself, and one press never publishes a commit
  it did not pin.
- It does not bump version files in the source.
