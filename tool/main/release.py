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
from common import is_junction, paths

from . import oneshot_android, specs

WORK = paths.RAW / "release-work"   # release-owned checkouts: server state, outside every task worktree home
SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
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
    found = re.fullmatch(r"v" + SEMVER.pattern, tag)
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
    return "%d.%d.%d" % chosen


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
    if minor > 999 or patch > 999 or major > 2099:
        raise ValueError(f"버전 {release} 는 versionCode 로 옮길 수 없다 — minor·patch 는 999, major 는 2099 까지다")
    return major * 1_000_000 + minor * 1000 + patch


# -- One attempt's checkout -----------------------------------------------------------------------

def pin(repo: Path, ref: str = "origin/main") -> str:
    """The commit an attempt builds, read once. A remote ref is fetched first."""

    if ref.startswith("origin/"):
        git(repo, "fetch", "origin", ref.removeprefix("origin/"), timeout=300)
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def place(repo: Path, attempt: str) -> Path:
    return WORK / repo.name / attempt


def checkout(repo: Path, sha: str, attempt: str) -> Path:
    """A fresh detached copy of `sha`, cloned rather than linked. The repository's worktree list never holds
    it, and nothing that cleans task worktrees reaches it."""

    into = place(repo, attempt) / "src"
    if into.exists():
        raise ValueError(f"`{into}` 가 이미 있다 — 시도마다 새로 받는다")
    into.parent.mkdir(parents=True, exist_ok=True)
    git(into.parent, "clone", "--quiet", "--shared", "--no-checkout", str(repo), str(into), timeout=600)
    git(into, "checkout", "--quiet", "--detach", sha, timeout=600)
    if git(into, "rev-parse", "HEAD").strip() != sha or git(into, "status", "--porcelain").strip():
        raise ValueError(f"`{into}` 가 {sha[:12]} 의 깨끗한 체크아웃이 아니다")
    return into


def discard(repo: Path, attempt: str) -> None:
    """The attempt's folder, removed only when it is `WORK`'s own and no folder on the way is a link."""

    folder = place(repo, attempt)
    node = Path(os.path.abspath(WORK))
    if not Path(os.path.abspath(folder)).is_relative_to(node):
        raise ValueError(f"{folder} 는 {WORK} 밖이다")
    for part in Path(os.path.abspath(folder)).relative_to(node).parts:
        node = node / part
        if node.is_symlink() or is_junction(node):
            raise ValueError(f"{node} 는 링크다 — 지우지 않는다")
    shutil.rmtree(folder, onexc=_writable)


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


def environment(kit: dict) -> dict:
    return {**os.environ, "JAVA_HOME": str(kit["java"]), "ANDROID_HOME": str(kit["sdk"]),
            "NDK_HOME": str(kit["ndk"]), "PATH": os.pathsep.join([str(kit["java"] / "bin"), os.environ.get("PATH", "")])}


def build(path: Path, android: dict, release: str, kit: dict, halt: threading.Event) -> Path:
    """The adapter's build command in the attempt's checkout. The version and its `versionCode` reach Tauri
    through its config overlay (`TAURI_CONFIG`), which writes `tauri.properties` for Gradle, and a build that is
    not Tauri's reads them from `RELEASE_VERSION` and `RELEASE_VERSION_CODE`. No source file is edited. The
    checks read what it produced."""

    overlay = {"version": release, "bundle": {"android": {"versionCode": code(release)}}}
    env = {**environment(kit), "TAURI_CONFIG": json.dumps(overlay), "RELEASE_VERSION": release,
           "RELEASE_VERSION_CODE": str(code(release))}
    exited, out, cut = specs.gate(android["build"], path, halt, env=env, timeout=BUILD_SECONDS, idle=QUIET_SECONDS)
    if exited != 0:
        tail = "\n".join(out.splitlines()[-40:])
        raise ValueError(f"빌드 실패 — {cut or f'{exited} 로 끝났다'}\n{tail}")
    apk = (path / android["artifact"]).resolve()
    if not apk.is_relative_to(path.resolve()):
        raise ValueError(f"`{android['artifact']}` 가 시도의 체크아웃 밖을 가리킨다")
    if not apk.is_file():
        raise ValueError(f"빌드가 `{android['artifact']}` 를 만들지 않았다")
    return apk


def run(argv: list, env: dict | None = None, timeout: float = 300) -> str:
    done = subprocess.run([str(a) for a in argv], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)
    if done.returncode:
        raise ValueError(f"{Path(str(argv[0])).name} 실패 — {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def test_key(folder: Path, kit: dict) -> dict:
    """A disposable key for a rehearsal, valid for one day and kept only in the attempt's folder."""

    password = secrets.token_urlsafe(24)
    store = folder / "rehearsal.jks"
    run([kit["keytool"], "-genkeypair", "-keystore", store, "-alias", "rehearsal", "-keyalg", "RSA",
         "-keysize", "2048", "-validity", "1", "-dname", "CN=wiki-agent release rehearsal",
         "-storepass:env", "RELEASE_KEY_PASSWORD", "-keypass:env", "RELEASE_KEY_PASSWORD"],
        {**environment(kit), "RELEASE_KEY_PASSWORD": password})
    return {"keystore": store, "alias": "rehearsal", "password": password}


def sign(apk: Path, out: Path, key: dict, kit: dict) -> Path:
    """Align, then sign with `key`. The password reaches the key tools only through their environment, never
    their command line and never the build."""

    aligned = out.with_suffix(".aligned.apk")
    run([kit["zipalign"], "-p", "-f", "4", apk, aligned])
    run([kit["apksigner"], "sign", "--ks", key["keystore"], "--ks-key-alias", key["alias"],
         "--ks-pass", "env:RELEASE_KEY_PASSWORD", "--out", out, aligned],
        {**environment(kit), "RELEASE_KEY_PASSWORD": key["password"]})
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
                "-storepass:env", "RELEASE_KEY_PASSWORD"], {**environment(kit), "RELEASE_KEY_PASSWORD": password})
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
    if seen["abis"] != [android["abi"]]:
        out.append(f"ABI {seen['abis']} 가 설정의 [{android['abi']!r}] 와 다르다")
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
    kit, attempt = tools(), f"rehearsal-{time.strftime('%Y%m%d-%H%M%S')}"
    sha = pin(repo, ref)
    cfg = settings(repo, sha)
    first = seed(repo, sha, cfg["version_source"])
    taken = git(repo, "tag", "--list").split()
    folder = place(repo, attempt)
    folder.mkdir(parents=True)
    key = test_key(folder, kit)
    cert = certificate(key["keystore"], key["alias"], key["password"], kit)
    published, previous, rounds = [], None, []
    try:
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
        refusals = {}
        if built:
            first_apk, release = rounds[0]["facts"], rounds[1]["version"]
            second = folder / f"2-{release}.apk"
            (folder / "other").mkdir()
            other = test_key(folder / "other", kit)
            try:
                stranger = facts(sign(second, folder / "2-other-key.apk", other, kit), kit)
            finally:
                Path(other["keystore"]).unlink(missing_ok=True)
            moved = {**cfg["android"], "application_id": cfg["android"]["application_id"] + ".moved"}
            refusals = {"certificate": refused(stranger, release, cfg["android"], cert, first_apk),
                        "application_id": refused(facts(second, kit), release, moved, cert,
                                                  {**first_apk, "package": moved["application_id"]})}
    finally:
        Path(key["keystore"]).unlink(missing_ok=True)   # the test key never outlives the rehearsal
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
