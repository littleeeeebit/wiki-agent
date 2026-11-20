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
| The release step | New module `tool/main/release.py`, the only caller that publishes production Releases. The Windows installer's checks are `tool/main/release_desktop.py`. |
| GitHub transfer | `tool/main/github_release.py`, a low-level helper both callers share. It runs `gh api` against the REST API, and every call names its repository as `owner/name`, so nothing reads a working directory's remote. A failure carries its HTTP status and says whether it is transient: no answer, 429, 5xx, or a 403 rate limit. Each caller keeps its own policy. S8 keeps its head-named draft prerelease on the default branch, its digest reuse and its replacement of the asset of the same name. A production release never replaces a complete asset. |
| Per-repository settings | An optional `[release]` table in `.wiki/adapter.toml`, read from the pinned commit through a typed reader. It names the one version-source file for the seed. It declares one or both kinds, and every press builds and ships each declared kind in one Release. `[release.android]` holds the build command, the artifact path, the application ID and the expected ABI, `none` for an app with no native code, which then needs no NDK. `[release.desktop]` holds the build command, the installer's path, where `{version}` stands for the version since Tauri names the installer after it, the app EXE's path, the product name and the publisher. The build command gets the version as `RELEASE_VERSION` and `RELEASE_VERSION_CODE`. A Tauri build passes `RELEASE_TAURI_CONFIG`, an overlay file beside the checkout, as one more `-c`. `TAURI_CONFIG` cannot carry the version, because the CLI replaces it with its own `-c` values. Step 1's rehearsal found this: it built the same bytes twice. The adapter's writer (`connect.adapter_text`) rewrites only `[slots]` and keeps every other table as written; a rewrite that would change another table is refused. |
| The checkout | A fresh detached checkout per attempt, in a release-owned directory outside the task worktree allocator, removed only by its own cleanup. The detached survey checkout (`refactor_survey`) is the precedent. |
| The ledger | Server state under `raw/releases/`, keyed by the canonical remote (`github.com/<owner>/<repo>`). Two windows or two checkouts of one repository share one reservation and one recovery record. `tool/main/release_publish.py` keeps it in `ledger.json` beside a lock. Each write replaces the whole file through a fresh staging file. A press records how many attempts the ledger held when it chose its version. If another attempt was added since, the reservation refuses the press. The live worker holds an OS lock on its attempt from before the reservation to its end, and a `finish` that finds the lock held leaves the attempt to that worker. A transient GitHub failure leaves the attempt pending, and so does a ledger write that fails after a remote write. `recover` carries every pending attempt on through the restart table, and the server calls it at start (`release_api.recover`), off the start path. |
| Signing (Android) | A release keystore per repository at `%LOCALAPPDATA%\wiki-agent-data\release-keys\<canonical remote>\release.jks`, outside every checkout. That location holds even unpackaged, because there the data root is the checkout. The password is random and lives only in this Windows account's Credential Manager (`wiki-agent release key <remote>`). It is given only to the key tools, through their environment, and is never handed to the adapter's build command or the repair agent. Those run as the same Windows account, and that account can read its own vault and profile, so the real boundary is the account. The person chose, on 2026-11-19, to publish from that one account: every repository released is theirs, and they accepted that its build and repair code could read the key's password and the GitHub login. One lock per repository serializes key creation across processes. Every check is made again under it. `certificate.sha256` beside the keystore is written last, by an atomic rename, and marks the key as established. After that, a lost keystore, a lost password or a changed certificate is refused, never replaced. A creation that stopped is finished only when the stored password opens the key it left. A keystore that no password opens is left for a person, staged or final. So is a surviving password with no keystore. Nothing deletes or overwrites either. The person backs up the keystore and the credential together. |
| Screen | A release card on the repository screen, opened by the project row's `배포` button (`web/src/components/ReleaseCard.tsx`, served by `tool/main/release_api.py`): last production version, commits ahead, the version each choice would publish, the last attempt, the test build, what a press ships, and where the established key is, for its backup. The publish button, pressed twice, publishes the version shown (`release_api.press`). One project runs one press or one test build at a time. |

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
  -> manifest: each asset's name, size and the sha256 its checks read, in the ledger;
               a file whose bytes changed since is refused; the exact files
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
| Version | `versionName` equals the version (`aapt dump badging`) | The installer's fixed version and `FileVersion`, and the installed app EXE's fixed version, equal the version, with `0.1.1.0` normalized to `0.1.1`. Each part of the version is at most 65535, as Windows' version resources hold it; Android's `versionCode` limits apply only when an APK ships too |
| Monotonic | `versionCode` strictly greater than the previous production APK's and equal to the version's code: Tauri's mapping, major × 1000000 + minor × 1000 + patch (`0.1.1` is `1001`), with minor and patch at most 999 and major at most 2099, so the code stays under Google Play's 2100000000. The step injects it with the version. | Version greater than the previous production installer's, both read as four integers; an unreadable previous version is refused |
| Identity | Application ID and ABI equal the adapter's and the previous production APK's of this kind; a release build, not debuggable | The installer is read once, as Tauri's default packs it: Unicode NSIS 3, its CRC on and matching, one solid LZMA stream within fixed size limits, checked before anything is read, and every record the script reads, the uninstaller's two included, lying whole in it. Anything else is refused. The script's registry writes must register, under `Software\Microsoft\Windows\CurrentVersion\Uninstall\<product>`, the product as `DisplayName`, the publisher as `Publisher` and the version as `DisplayVersion`, and must write `$INSTDIR` under `Software\<publisher>\<product>`, where the next update looks for the install folder. Each is a string written in `SHCTX`, and every write of that key and name, compared without case as Windows compares them, agrees. Where `SHCTX` resolves must be fixed by the script and equal the previous production installer's: the install scope `SetShellVarContext` sets once (Tauri's `installMode`, `currentUser` or `perMachine`; `both` is decided at run time, so it is refused) and the registry view `SetRegView` sets. The main binary it registers (`MainBinaryName`) is read from the script's one file entry that lands at `$INSTDIR\<MainBinaryName>`, resolved from its name and the `SetOutPath` before it as Windows resolves paths, where no other entry writes a file Windows would take for the same one, and must be the very EXE the build made, with the bundle-type placeholder Tauri sets, built for x64 (its PE `Machine` is AMD64), as the asset's name says. Product name, in the installer and that binary, equals the adapter's and the previous production installer's. Publisher, that binary's `CompanyName`, equals the adapter's and the previous production installer's, read the same way from its bytes. |
| Signature | `apksigner verify --print-certs` matches the repository's expected certificate and the previous production APK's | none until code signing is planned, so Windows warns before running the installer; the card says so |
| Digest | `sha256` of the final bytes, the same bytes uploaded | the same |

The installer checks cover Tauri's default NSIS template, which is the one
supported. They read what that template compiles to, and they catch the
drift a merge brings by mistake: a renamed product or publisher, a moved
scope or view, a main binary that is not the built one, a damaged or
truncated file. They do not interpret NSIS control flow. A custom template
or installer hook that the repository's owner writes to move files at run
time is outside what they prove. Each repository's owner writes its own
build, so the press guards against mistakes, not against that owner.

Identity is checked against the previous production artifact of the same
kind, not only against the adapter at the pinned commit. A merge that changes
the Gradle application ID and the adapter together would otherwise pass, and
Android would install the result as a separate app. The first production
release of a kind has no previous artifact, so it is checked against the
configured identity and expected certificate. Those become the baseline. The
previous artifact of a kind is the one in the last production Release that
shipped that kind, whatever came since, and a previous APK is found whatever
its ABI, so a changed ABI is refused rather than taken for a first release.
The first production APK makes the release key, even when an earlier Release
shipped only an installer; after it the key is opened, never made again.

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
  names that repair task, whose pull request the task list shows, or why it did
  not start. The fix's merge needs its own authorization, as
  every merge does. Publishing the merged fix is a new release press, because
  one press authorizes only the commit it pinned.

What needs a source change is narrow:

- The build command exits with an error, makes no artifact, or makes one
  `aapt2` cannot read as an APK (`release.BuildFailed`). The check happens
  before signing, so a broken product is the build's failure.
- The built APK's versionName, versionCode, application ID or ABI is wrong,
  or the APK is debuggable (`release.built_wrong`).
- The installer or the app EXE is missing, or cannot be read: no version
  resource, not Tauri's default NSIS packing, a CRC that does not match, or
  no registered main binary inside it that equals the built EXE. Or its version, product name or publisher is wrong
  (`release_desktop.built_wrong`). A previous installer that cannot be read
  is not the source's to fix, and ends the press without a repair.

A wrong certificate is the release key's problem (`release.signed_wrong`).
So are a missing release tool, a key the server refuses, and a git or GitHub
failure. A build that a person stopped, or that hit its deadline or went
silent, is the machine's. So is one whose shell reports, in the log's last
lines, that it cannot find a program by bare name that is not on the build's
`PATH` (`release.lacks_tool`). The message is the shell's own: the English
ones, and what this machine's shells answer, in their language, when the
server asks them once through the gate for a program that exists nowhere
(`release.learned_shapes`). They are asked in the build's own environment,
without its secrets, and a stop ends the asking and still counts as a stop.
The asking is best effort: a failure in it leaves the build's own error to
decide, and only a complete answer, a message from every shell, is kept for
later builds; otherwise the next failed build asks again. A source error
that merely quotes a name, such
as `'theme' is not exported` or its Korean counterpart, is not one. A missing
script given by path is the
repository's, and a source change can restore it. None of the machine's
failures goes to an agent. Nor does a release the person stopped at any moment
before the hand-off, its cleanup included (`release.handed`); its failure stays
recorded with why no repair runs.

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

The agent is never given the key or its password. A GitHub login the
account holds stays readable to that account, a risk the person accepted
in choosing to publish from one account ("Signing (Android)"). A repository the
server does not know as a project gets no task: the turn's end finds the
task's project by name, so such a task would never leave `작업 중`. The
ledger records the task's ID under the attempt's `repair`. When its first
turn cannot start, the task waits in its worktree, and the ledger records
both its ID and the refusal. `release_rehearsal.rehearse` hands its failures over the
same way, so the unpublished path exercises the hand-off. With a repair,
it rehearses only a branch that `origin` has, since that branch is where
the repair's pull request goes.

## Existing debug installations

Phones that installed the translator's debug APKs carry a debug certificate.
The first production APK, signed with the release key under the same
application ID, cannot update them: the user uninstalls once and installs the
release, which loses the debug app's data. The application ID stays as it
is. The notes of the first Release that ships an APK say this
(`release.FIRST_RELEASE_NOTE`), and step 3 puts that note in the Release.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Android build and verify, unpublished | The typed `[release]` reader, the seed and the one version selector for card and attempt (skipping a taken version, including a failed first release's seed; refusing a press whose version changed by pin time), pinning, the release-owned detached checkout, the release build with the injected version. A disposable test key and its certificate sign the step's APKs, so every Android check runs over two consecutive versions on a disposable target, including the refusal of a changed application ID and of a changed certificate. Nothing is published, and the test key never signs a production artifact. | Done: `tool/main/release.py`. The rehearsal on a disposable clone of the translator built 0.1.0 (versionCode 1000) and 0.1.1 (1001) from one SHA. It refused a second key and a moved application ID on the real bytes. |
| 2 | Signing | The per-repository release keystore and expected certificate, set up once; signing outside the build command; the debug-installation note. Required before anything is published. | Done: `release.release_key` and the `key` command in `tool/main/release.py`. A throwaway remote's key was made in Credential Manager. An APK signed with it verified against the recorded certificate, and the key and credential were then removed. No real repository has a key yet; the first is made once, on request. |
| 3 | Transfer and ledger | The shared GitHub transfer helper, which S8 moves onto with its own policy unchanged; the `raw/releases/` ledger, the reservation, the manifest, the tag read-back, the draft ID, every row of the restart table including a lost publish answer, and a failed attempt's draft and tag left in place and recorded | Done: `tool/main/github_release.py` and `tool/main/release_publish.py`. A clone of the translator, pushed as `main` of the private repository `LittleBitAI/wiki-agent-release-rehearsal`, was published twice from one SHA. The first press made v0.1.0 (versionCode 1000) with the first-release note and created that remote's key. The patch press made v0.1.1 (1001). It was checked against v0.1.0's APK downloaded from the Release. Each tag reads back at the pinned SHA, and each asset's GitHub digest equals its manifest entry. The rehearsal key and credential were removed afterwards, so that repository takes no further releases. The restart rows run over a fake GitHub in `tool/test_release_publish.py`. |
| 4 | Failure to agent | Bounded retries and the agent turn whose fix goes through the ordinary review loop, exercised on the unpublished path | Done: `release_publish.WAITS` and `_tried`, `release.BuildFailed` with `built_wrong` and `signed_wrong`, and `tool/main/release_repair.py`. The live check committed an import of a missing module to `main` of `LittleBitAI/wiki-agent-release-rehearsal`. The rehearsal's build failed on it and made the task `release-fix-0-1-2`. Its agent turn removed the import, passed the repository's gate (165 Rust and 30 frontend tests), and the round check opened that repository's PR #1. The PR was closed unmerged. Two earlier runs found that a repository the server does not know never finishes its task, now refused, and that the translator tracks `.wiki/.sync`, which the wiki hook rewrites every turn. |
| 5 | Button and card | The repository card and its button, the one entry point that publishes; the seed or the bump choice; `recover` at server start. The person chose, on 2026-11-19, to ship the card with the unpublished test build first and the publish button in 5b. | Done: `tool/main/release_api.py` and `web/src/components/ReleaseCard.tsx`. The test build is `release_rehearsal.rehearse` on origin's `main`, the commit the card shows, its source failures handed to `release_repair.start`. The start carries the commit the card showed, and a `main` that moved since is refused. Every command before the hand-off ends within a second of a stop (`release.stoppable`). A stop is refused once the hand-off has begun. Server shutdown admits no new test build, stops a running one, lets a begun repair finish starting, and waits for the cleanup. Whatever a test build could not remove is shown on the card, however it ended. |
| 5b | Publish | The card's publish button calls `release_publish.publish` with the shown version and choice, from the one Windows account ("Signing (Android)"). | Done: `release_api.press` runs the press off the request, and the card follows its attempt in the ledger, read again every fifteen seconds while it runs. The second press sends exactly what the first confirmed, the version, the choice, the canonical remote and the commit, and only while the card still shows them; `release_publish.publish` refuses another origin or a moved `main` before reserving. The key is looked up under that checked remote. A stop is checked between phases and before each GitHub write, each asset delete and upload included, and no write launches once shutdown has signalled it (`github_release.stop`): before any remote write the attempt ends failed, after one it stays pending. The startup sweep belongs to its lifespan: shutdown halts and waits for it, and no press, resume or test build starts while it runs. A pending attempt with no press on it is carried on from the card (`release_api.resume`), naming the remote and attempt the card showed, which must be that project's; the ledger keeps it on the card when the preview fails. The card shows only redacted diagnostics (`release_api.public`) and a small view of the attempt, and follows the followed server's press only. Once the key is established, the card names the keystore and the credential to back up. No real repository has been published yet. |
| 6 | Windows installer | The `desktop` kind's NSIS build and its checks | Done: `[release.desktop]` in `release.settings`, the kinds' shared `release.build`, and `tool/main/release_desktop.py`, which reads the version resources through Windows. A press builds every declared kind from the one checkout and ships them in one Release, `<repo>-<version>-x64-setup.exe` beside the APK, and a repository that ships no APK needs no key or Android tools. The rehearsal checks two consecutive installers, and a renamed product name and publisher on the second one's real bytes. The rehearsal on a disposable clone of the translator's `main`, with a committed `[release.desktop]` table, built 0.1.0 and 0.1.1 from one SHA through `cargo tauri build --bundles nsis`. Each installer carried its version and the product name, and held the very app EXE the build made, with its version and the publisher Tauri derives from the identifier (`littlebit`); the script named both. The renamed identity was refused on the second installer's real bytes. Nothing was published, and no key was made. |
| 7 | Oneshot writes `[release]` for Android | S8's README turn writes `[release]` and `[release.android]` for an Android app, keeping every other adapter table. The person chose, on 2026-11-20, to check the table with one real build before the first pull request, and to do Android first. | Done: `oneshot_pr.release_checked`, after the final gate. It reads the table as a press reads it at the gate's HEAD, holds `application_id` to the run's app, refuses a seed a declared kind cannot carry, and refuses `[release.desktop]` until 7b. It then builds the unsigned release APK once with the patch after the seed, so a build that keeps the source's version fails, from a fresh checkout of that HEAD, every command within a stop's reach (`release.checkout`, `release.build`), and checks it with `release.built_wrong`: versionName, versionCode, application ID, ABI and not debuggable. No key is made. A refusal sends the README turn back before the APK is frozen; that turn alone, not the pull request's text turn, may write the adapter (`oneshot_build.GRANTS`). The first real run on a oneshot-made app comes with the oneshot's live Android run (`docs/plans/oneshot/7-android.md`, 7g). |
| 7b | Oneshot writes `[release.desktop]` | The same for a Windows app, once the oneshot's S8 ships its installer (`docs/plans/oneshot/9-kinds.md`, 9e6) | Not started |

## What this does not do

- It does not publish to a store (Play Store, Microsoft Store), and it does
  not push an update to installed apps. An in-app update check against the
  latest Release is a separate plan.
- It does not run on a merge by itself, and one press never publishes a commit
  it did not pin.
- It does not bump version files in the source.
