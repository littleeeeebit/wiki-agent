"""Release step 1 (`docs/plans/release/0-overview.md`). It builds one pinned commit into an Android APK with the
version injected, signs it and checks it. Nothing here publishes. Steps 2 to 5 bring the release key, the GitHub
transfer and ledger, failure handling and the button. Until they land, `rehearse` signs with a disposable test
key, and that key never signs a production artifact.

A rehearsal runs from `tool/` as this module's `rehearse` command, given a disposable clone and an optional
`--ref`."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import android_owned
from common import errorlog, is_junction, paths

from . import oneshot_android, specs

WORK = paths.RAW / "release-work"   # release-owned checkouts: server state, outside every task worktree home
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)   # SemVer's core: ASCII, no leading zeros
BUMPS = ("patch", "minor", "major")
BUILD_SECONDS, QUIET_SECONDS = 3600, 1800   # a Rust and Gradle build is long and can be silent for minutes
ANDROID = ("build", "artifact", "application_id", "abi")


def git(path: Path, *args: str, timeout: float = 120) -> str:
    done = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if done.returncode:
        raise ValueError(f"git {' '.join(args[:2])} 실패 — {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout


# -- What a commit declares ----------------------------------------------------------------------

def settings(repo: Path, sha: str) -> dict:
    """The `[release]` table of `.wiki/adapter.toml` as the pinned commit has it, never as the checkout does."""

    try:
        table = tomllib.loads(git(repo, "show", f"{sha}:.wiki/adapter.toml")).get("release")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"`.wiki/adapter.toml` 을 읽지 못했다 — {exc}") from exc
    if not isinstance(table, dict):
        raise ValueError(f"{sha[:12]} 의 `.wiki/adapter.toml` 에 `[release]` 표가 없다")
    android = table.get("android")
    if not isinstance(android, dict):
        raise ValueError("`[release.android]` 표가 없다")
    wrong = [k for k in ("version_source",) if not _text(table.get(k))] + \
            [f"android.{k}" for k in ANDROID if not _text(android.get(k))]
    if wrong:
        raise ValueError(f"`[release]` 의 글줄 칸이 비었거나 글줄이 아니다: {', '.join(wrong)}")
    return {"version_source": table["version_source"], "android": {k: android[k] for k in ANDROID}}


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def seed(repo: Path, sha: str, source: str) -> str:
    """The first release's version: the one file `version_source` names, read at the pinned commit."""

    text = git(repo, "show", f"{sha}:{source}")
    try:
        if source.endswith(".json"):
            found = json.loads(text).get("version")
        elif source.endswith(".toml"):
            data = tomllib.loads(text)
            found = (data.get("package") or {}).get("version") or data.get("version")
        else:
            raise ValueError(f"`{source}` 는 .json 도 .toml 도 아니다")
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"`{source}` 에서 버전을 읽지 못했다 — {exc}") from exc
    if not isinstance(found, str) or not SEMVER.fullmatch(found):
        raise ValueError(f"`{source}` 의 버전 {found!r} 은 major.minor.patch 가 아니다")
    return found


# -- Which version a press publishes --------------------------------------------------------------

def parsed(tag: str) -> tuple[int, int, int] | None:
    found = re.fullmatch(r"v" + SEMVER.pattern, tag, re.ASCII)
    return tuple(map(int, found.groups())) if found else None


def version(releases: list[dict], taken: list[str], bump: str | None, first: str) -> str:
    """The one selector the card and the attempt share. The last eligible production release, bumped. Before
    the first one, the seed, with no choice. Then the next free patch past any tag already on the remote."""

    eligible = [v for r in releases if not r.get("isDraft") and not r.get("isPrerelease")
                and (v := parsed(r.get("tagName") or ""))]
    if eligible:
        if bump not in BUMPS:
            raise ValueError(f"`{bump}` 는 {', '.join(BUMPS)} 가 아니다")
        major, minor, patch = max(eligible)
        chosen = {"major": (major + 1, 0, 0), "minor": (major, minor + 1, 0), "patch": (major, minor, patch + 1)}[bump]
    else:
        if bump is not None:
            raise ValueError("첫 정식 릴리스는 seed 버전으로만 나간다 — 올릴 버전이 아직 없다")
        chosen = tuple(map(int, SEMVER.fullmatch(first).groups()))
    used = {v for t in taken if (v := parsed(t))}
    while chosen in used:
        chosen = (chosen[0], chosen[1], chosen[2] + 1)
    release = "%d.%d.%d" % chosen
    code(release)   # a version Android cannot carry is refused here, before the card shows it
    return release


def accepted(shown: str, releases: list[dict], taken: list[str], bump: str | None, first: str) -> str:
    """The version a press publishes, recomputed at pin time. A tag that appeared since the card was drawn
    changes it, and then the press is refused before anything is reserved or written."""

    now = version(releases, taken, bump, first)
    if now != shown:
        raise ValueError(f"카드의 버전 {shown} 이 지금은 {now} 이다 — 다시 누르면 {now} 로 나간다")
    return now


def code(release: str) -> int:
    """Android's `versionCode` for a version: Tauri's own mapping, major * 1000000 + minor * 1000 + patch.
    Minor and patch stay under 1000 and the result under Google Play's 2100000000, so the code rises with
    every version and never collides."""

    major, minor, patch = map(int, SEMVER.fullmatch(release).groups())
    if minor > 999 or patch > 999 or major > 2099 or (major, minor, patch) == (0, 0, 0):
        raise ValueError(f"버전 {release} 는 versionCode 로 옮길 수 없다 — 0.0.0 은 안 되고, "
                         "minor·patch 는 999, major 는 2099 까지다")
    return major * 1_000_000 + minor * 1000 + patch


# -- One attempt's checkout -----------------------------------------------------------------------

def pin(repo: Path, ref: str = "origin/main") -> str:
    """The commit an attempt builds, read once. A remote branch is fetched into a ref of this attempt's own,
    so neither the remote's fetch mapping nor another fetch running at the same time can change what it reads."""

    if not ref.startswith("origin/"):
        return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    own = f"refs/release-pin/{secrets.token_hex(8)}"
    try:   # from before the fetch: one that wrote the ref and then timed out leaves nothing either
        git(repo, "fetch", "--no-tags", "origin", f"+refs/heads/{ref.removeprefix('origin/')}:{own}", timeout=300)
        return git(repo, "rev-parse", "--verify", f"{own}^{{commit}}").strip()
    finally:
        try:
            git(repo, "update-ref", "-d", own)
        except (ValueError, subprocess.SubprocessError) as exc:
            # Recorded, never raised: the fetch's own error, if any, is the one that matters, and the name is
            # never reused, so the leftover cannot change a later pin.
            errorlog.record("release-pin", exc, repo=str(repo), ref=own)


def place(repo: Path, attempt: str) -> Path:
    return WORK / repo.name / attempt


def checkout(repo: Path, sha: str, attempt: str) -> Path:
    """A fresh detached copy of `sha`, cloned rather than linked. The repository's worktree list never holds
    it, and nothing that cleans task worktrees reaches it."""

    into = owned(place(repo, attempt) / "src")
    if into.exists():
        raise ValueError(f"`{into}` 가 이미 있다 — 시도마다 새로 받는다")
    into.parent.mkdir(parents=True, exist_ok=True)
    git(into.parent, "clone", "--quiet", "--shared", "--no-checkout", str(repo), str(into), timeout=600)
    git(into, "checkout", "--quiet", "--detach", sha, timeout=600)
    if git(into, "rev-parse", "HEAD").strip() != sha or git(into, "status", "--porcelain").strip():
        raise ValueError(f"`{into}` 가 {sha[:12]} 의 깨끗한 체크아웃이 아니다")
    return into


def owned(path: Path) -> Path:
    """`path` when it lies strictly under `WORK` and no folder from the data root down to it is a link or a
    junction. Checked before a checkout is written and before one is removed."""

    root, target = Path(os.path.abspath(WORK)), Path(os.path.abspath(path))
    if target == root or not target.is_relative_to(root):
        raise ValueError(f"{target} 는 {root} 안이 아니다")
    data = Path(os.path.abspath(paths.DATA))
    node = data if root.is_relative_to(data) else root.parent
    for part in ("", *target.relative_to(node).parts):
        node = node / part if part else node
        if node.is_symlink() or is_junction(node):
            raise ValueError(f"{node} 는 링크다 — 배포 작업 폴더는 링크를 따라가지 않는다")
    return target


def discard(repo: Path, attempt: str) -> None:
    """The attempt's folder, removed only when `owned` holds for it."""

    folder = owned(place(repo, attempt))
    # `onexc` arrived in Python 3.12; the repository still runs on 3.11.
    shutil.rmtree(folder, **({"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}))


def _writable(func, path, exc) -> None:
    """Git keeps its objects read-only, which stops Windows from deleting them."""

    os.chmod(path, 0o700)
    func(path)


# -- Build, sign, inspect -------------------------------------------------------------------------

def tools() -> dict:
    """The JDK, the SDK with the build tools, and the newest NDK, as the build and the checks use them."""

    homes = [os.environ.get("JAVA_HOME"), str(oneshot_android.ROOT / "jdk-17")]
    java = next((Path(h) for h in homes if h and oneshot_android.java(Path(h))), None)
    names = (f"aapt2{oneshot_android.EXE}", f"zipalign{oneshot_android.EXE}", f"apksigner{oneshot_android.BAT}")
    sdk = next((r for r in oneshot_android.roots()
                if all((r / "build-tools" / oneshot_android.BUILD_TOOLS / n).is_file() for n in names)), None)
    ndks = sorted((sdk / "ndk").iterdir()) if sdk and (sdk / "ndk").is_dir() else []
    gone = [what for what, have in (("JDK 17 (JAVA_HOME)", java), (f"build-tools {oneshot_android.BUILD_TOOLS}", sdk),
                                    ("NDK", ndks)) if not have]
    if gone:
        raise ValueError(f"Android 배포 도구가 없다: {', '.join(gone)}")
    bin_ = sdk / "build-tools" / oneshot_android.BUILD_TOOLS
    return {"java": java, "sdk": sdk, "ndk": ndks[-1], "aapt2": bin_ / names[0], "zipalign": bin_ / names[1],
            "apksigner": bin_ / names[2], "keytool": java / "bin" / f"keytool{oneshot_android.EXE}"}


SECRET = "RELEASE_KEY_PASSWORD"   # reaches the key tools only, set by `keyed`; never inherited


def environment(kit: dict) -> dict:
    """The tools' environment, without any key password the server itself was started with."""

    return {**{k: v for k, v in os.environ.items() if k.upper() != SECRET}, "JAVA_HOME": str(kit["java"]),
            "ANDROID_HOME": str(kit["sdk"]), "NDK_HOME": str(kit["ndk"]),
            "PATH": os.pathsep.join([str(kit["java"] / "bin"), os.environ.get("PATH", "")])}


def keyed(kit: dict, password: str) -> dict:
    return {**environment(kit), SECRET: password}


def build(path: Path, android: dict, release: str, kit: dict, halt: threading.Event) -> Path:
    """The adapter's build command in the attempt's checkout, given the version as `RELEASE_VERSION` and
    `RELEASE_VERSION_CODE`. A Tauri build passes `RELEASE_TAURI_CONFIG`, an overlay file beside the checkout, as
    one more `-c`. Tauri then writes `tauri.properties` for Gradle. `TAURI_CONFIG` cannot carry it, because the
    CLI replaces that variable with its own `-c` values. No source file is edited. The checks read what the
    build produced, so a command that drops the version is refused there."""

    def output() -> Path:
        apk = (path / android["artifact"]).resolve()
        if not apk.is_relative_to(path.resolve()):
            raise ValueError(f"`{android['artifact']}` 가 시도의 체크아웃 밖을 가리킨다")
        return apk

    if output().exists():   # a file the commit carries is not this build's output
        raise ValueError(f"`{android['artifact']}` 가 빌드 전부터 체크아웃에 있다 — 빌드가 만든 파일만 검사한다")
    overlay = path.parent / "tauri.release.json"
    # Off, or a target's `autoIncrementVersionCode` would replace the code the version maps to.
    android_overlay = {"versionCode": code(release), "autoIncrementVersionCode": False}
    overlay.write_text(json.dumps({"version": release, "bundle": {"android": android_overlay}}), encoding="utf-8")
    env = {**environment(kit), "RELEASE_VERSION": release, "RELEASE_VERSION_CODE": str(code(release)),
           "RELEASE_TAURI_CONFIG": str(overlay)}
    exited, out, cut = specs.gate(android["build"], path, halt, env=env, timeout=BUILD_SECONDS, idle=QUIET_SECONDS)
    if exited != 0:
        tail = "\n".join(out.splitlines()[-40:])
        raise ValueError(f"빌드 실패 — {cut or f'{exited} 로 끝났다'}\n{tail}")
    apk = output()
    if not apk.is_file():
        raise ValueError(f"빌드가 `{android['artifact']}` 를 만들지 않았다")
    return apk


def run(argv: list, env: dict | None = None, timeout: float = 300) -> str:
    done = subprocess.run([str(a) for a in argv], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)
    if done.returncode:
        raise ValueError(f"{Path(str(argv[0])).name} 실패 — {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def test_key(store: Path, kit: dict) -> dict:
    """A disposable key for a rehearsal at `store`, valid for one day. The caller removes `store` whatever
    happens, because `keytool` can write it and still fail."""

    password = secrets.token_urlsafe(24)
    run([kit["keytool"], "-genkeypair", "-keystore", store, "-alias", "rehearsal", "-keyalg", "RSA",
         "-keysize", "2048", "-validity", "1", "-dname", "CN=wiki-agent release rehearsal",
         "-storepass:env", SECRET, "-keypass:env", SECRET],
        keyed(kit, password))
    return {"keystore": store, "alias": "rehearsal", "password": password}


def sign(apk: Path, out: Path, key: dict, kit: dict) -> Path:
    """Align, then sign with `key`. The password reaches the key tools only through their environment, never
    their command line and never the build."""

    aligned = out.with_suffix(".aligned.apk")
    run([kit["zipalign"], "-p", "-f", "4", apk, aligned], environment(kit))
    run([kit["apksigner"], "sign", "--ks", key["keystore"], "--ks-key-alias", key["alias"],
         "--ks-pass", f"env:{SECRET}", "--out", out, aligned],
        keyed(kit, key["password"]))
    aligned.unlink(missing_ok=True)
    return out


def badging(text: str) -> dict:
    """What `aapt2 dump badging` says about identity and version."""

    head = re.search(r"^package: name='([^']*)' versionCode='(\d+)' versionName='([^']*)'", text, re.M)
    if not head:
        raise ValueError("`aapt2 dump badging` 에 package 줄이 없다")
    abis = re.search(r"^native-code: (.*)$", text, re.M)
    return {"package": head.group(1), "code": int(head.group(2)), "name": head.group(3),
            "abis": re.findall(r"'([^']+)'", abis.group(1)) if abis else [],
            "debuggable": bool(re.search(r"^application-debuggable", text, re.M))}


def certificates(text: str) -> list[str]:
    """The signers' SHA-256 digests from `apksigner verify --print-certs`."""

    return [d.lower() for d in re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)\s*$", text, re.M)]


def facts(apk: Path, kit: dict) -> dict:
    env = environment(kit)
    return {**badging(run([kit["aapt2"], "dump", "badging", apk], env)),
            "certs": certificates(run([kit["apksigner"], "verify", "--print-certs", apk], env)),
            "sha256": android_owned.sha256(apk)}


def certificate(store: Path, alias: str, password: str, kit: dict) -> str:
    """The SHA-256 of `alias`'s certificate in `store`, as `apksigner` prints signer digests."""

    text = run([kit["keytool"], "-list", "-v", "-keystore", store, "-alias", alias,
                "-storepass:env", SECRET], keyed(kit, password))
    found = re.search(r"SHA256:\s*([0-9A-F:]+)", text)
    if not found:
        raise ValueError("keytool 이 인증서 SHA-256 을 주지 않았다")
    return found.group(1).replace(":", "").lower()


def refused(seen: dict, release: str, android: dict, cert: str, previous: dict | None = None) -> list[str]:
    """Why this APK cannot be published, empty when it can. `previous` is the last production APK of the
    kind. Without one, the configured identity and the expected certificate are the baseline."""

    out = []
    if seen["name"] != release:
        out.append(f"versionName {seen['name']!r} 가 버전 {release!r} 와 다르다")
    if seen["code"] != code(release):
        out.append(f"versionCode {seen['code']} 가 버전 {release} 의 {code(release)} 와 다르다")
    if previous and seen["code"] <= previous["code"]:
        out.append(f"versionCode {seen['code']} 가 이전 정식 APK 의 {previous['code']} 보다 크지 않다")
    for name, want in (("설정", android["application_id"]), *((("이전 정식 APK", previous["package"]),) if previous else ())):
        if seen["package"] != want:
            out.append(f"앱 ID {seen['package']!r} 가 {name}의 {want!r} 와 다르다 — Android 는 다른 앱으로 설치한다")
    for name, want in (("설정", [android["abi"]]), *((("이전 정식 APK", previous["abis"]),) if previous else ())):
        if seen["abis"] != want:
            out.append(f"ABI {seen['abis']} 가 {name}의 {want} 와 다르다 — 기존 기기가 업데이트를 받지 못한다")
    if seen["debuggable"]:
        out.append("디버그 빌드다 — 정식 릴리스는 debuggable 이 아니다")
    for name, want in (("기대한 인증서", [cert]), *((("이전 정식 APK 의 인증서", previous["certs"]),) if previous else ())):
        if seen["certs"] != want:
            out.append(f"서명 인증서 {seen['certs']} 가 {name} {want} 와 다르다 — 설치된 앱이 업데이트되지 않는다")
    return out


# -- The unpublished rehearsal --------------------------------------------------------------------

def rehearse(repo: Path, ref: str = "main", halt: threading.Event | None = None) -> dict:
    """Two consecutive versions of `ref` built, signed with one test key and checked, the second against the
    first as its previous production APK. Each build gets its own fresh checkout. Nothing is published. The
    taken tags are the local ones, since a disposable target has no GitHub releases to read.

    Then the two refusals that keep an installed app updatable, on the second APK's real bytes: re-signed with
    a second test key, and checked against an application ID other than the one it was built with."""

    halt = halt or threading.Event()
    kit, attempt = tools(), f"rehearsal-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"   # two in one second
    sha = pin(repo, ref)
    cfg = settings(repo, sha)
    first = seed(repo, sha, cfg["version_source"])
    taken = git(repo, "tag", "--list").split()
    folder = owned(place(repo, attempt))
    folder.mkdir(parents=True)
    stores = (folder / "rehearsal.jks", folder / "other.jks")
    published, previous, rounds, refusals = [], None, [], {}
    try:   # from before the first key exists: every test keystore goes, whatever fails
        key = test_key(stores[0], kit)
        cert = certificate(key["keystore"], key["alias"], key["password"], kit)
        for n in (1, 2):
            release = version(published, taken, None if not published else "patch", first)
            src = checkout(repo, sha, f"{attempt}/{n}")
            signed = sign(build(src, cfg["android"], release, kit, halt), folder / f"{n}-{release}.apk", key, kit)
            seen = facts(signed, kit)
            why = refused(seen, release, cfg["android"], cert, previous)
            rounds.append({"version": release, "facts": seen, "refused": why})
            discard(repo, f"{attempt}/{n}")   # the checkout and its build outputs are large; the signed APK stays
            if why:
                break
            published.append({"tagName": f"v{release}"})
            previous = seen
        built = len(rounds) == 2 and not any(r["refused"] for r in rounds)
        if built:
            first_apk, release = rounds[0]["facts"], rounds[1]["version"]
            second = folder / f"2-{release}.apk"
            stranger = facts(sign(second, folder / "2-other-key.apk", test_key(stores[1], kit), kit), kit)
            moved = {**cfg["android"], "application_id": cfg["android"]["application_id"] + ".moved"}
            refusals = {"certificate": refused(stranger, release, cfg["android"], cert, first_apk),
                        "application_id": refused(facts(second, kit), release, moved, cert,
                                                  {**first_apk, "package": moved["application_id"]})}
    finally:
        left = []
        for store in stores:   # a test key never outlives the rehearsal; one locked file never spares the other
            try:
                store.unlink(missing_ok=True)
            except OSError as exc:
                left.append(f"{store}: {exc}")
        if left:
            raise OSError(f"시험 키를 지우지 못했다 — 직접 지워라: {'; '.join(left)}")
    caught = bool(refusals) and all(len([w for w in why if marker in w]) == want for why, marker, want in (
        (refusals.get("certificate", []), "서명 인증서", 2), (refusals.get("application_id", []), "앱 ID", 2)))
    return {"sha": sha, "seed": first, "cert": cert, "folder": str(folder), "rounds": rounds,
            "refusals": refusals, "ok": built and caught}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 2 or argv[0] != "rehearse":
        print("usage: rehearse <repo> [--ref <ref>]", file=sys.stderr)
        return 2
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else "main"
    result = rehearse(Path(argv[1]).resolve(), ref)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
