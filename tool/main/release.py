"""Release steps 1, 2 and 6 (`docs/plans/release/0-overview.md`). Step 1 builds one pinned commit into an Android
APK with the version injected, signs it and checks it; step 6's Windows installer is `release_desktop`'s. Step 2
adds each repository's one release key. Nothing here publishes: `release_publish` does, with step 3's transfer
and ledger. The rehearsal (`release_rehearsal.rehearse`) signs only with disposable test keys, and a test key
never signs a production artifact. It runs from `tool/` as this module's `rehearse` command, given a disposable
clone and an optional `--ref`."""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from contextlib import contextmanager, nullcontext
from ctypes import wintypes
from pathlib import Path

import android_owned
import verify
from common import errorlog, host, is_junction, paths, process

from . import oneshot_android, release_desktop, specs

WORK = paths.RAW / "release-work"   # release-owned checkouts: server state, outside every task worktree home
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)   # SemVer's core: ASCII, no leading zeros
BUMPS = ("patch", "minor", "major")
BUILD_SECONDS, QUIET_SECONDS = 3600, 1800   # a Rust and Gradle build is long and can be silent for minutes
ANDROID = ("build", "artifact", "application_id", "abi")
DESKTOP = ("build", "artifact", "exe", "product_name", "publisher")   # `artifact` may name `{version}`
KINDS = {"android": ANDROID, "desktop": DESKTOP}


class Stopped(RuntimeError):
    """A command ended because the run it belongs to was stopped: by the person, or by the server's shutdown."""


_stop = threading.local()   # the calling thread's halt while `stoppable` holds it


@contextmanager
def stoppable(halt: threading.Event | None):
    """Every `git` and `run` on this thread, while this holds, killed with its whole process tree within a
    second of `halt`, so a stop or a shutdown never waits out a command's own timeout. `None` lifts it for a
    cleanup that must run even after the stop; the scope it was inside comes back after."""

    outer = getattr(_stop, "halt", None)
    _stop.halt = halt
    try:
        yield
    finally:
        _stop.halt = outer


def ran(argv: list, timeout: float, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    halt = getattr(_stop, "halt", None)
    if halt is None:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    name = Path(str(argv[0])).name
    if halt.is_set():   # stopped already: nothing new starts
        raise Stopped(f"멈춤 — {name} 을 시작하지 않았다")
    # Started suspended, so a child it starts is in its job before it can leave (`process.contained`).
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=process.SUSPENDED,
                            start_new_session=os.name != "nt")
    job = None
    try:
        job = process.contained(proc)
        if os.name == "nt" and not job:   # a tree it cannot own never runs (`runtime` refuses the same)
            raise OSError(f"{name} 의 프로세스 트리를 맡지 못했다 — 실행하지 않았다")
        process.resumed(proc)
        deadline = time.monotonic() + timeout
        while not halt.is_set() and time.monotonic() < deadline:
            try:
                out, err = proc.communicate(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
                return subprocess.CompletedProcess(argv, proc.returncode, out, err)
            except subprocess.TimeoutExpired:   # a retry after its timeout loses no output
                pass
        if halt.is_set():
            raise Stopped(f"멈춤 — {name} 을 끝냈다")
        raise subprocess.TimeoutExpired(argv, timeout)
    finally:   # the whole tree ended and waited for, whatever ended the command, before the caller cleans up
        try:
            if proc.poll() is None:
                process.killed(job)
                proc.kill()
                proc.communicate(timeout=10)   # bounded: a descendant holding the pipes cannot keep it
        except (OSError, subprocess.SubprocessError) as exc:   # recorded, never over what ended the command
            errorlog.record("release-command", exc, command=name)
        finally:
            process.terminated(job)


def git(path: Path, *args: str, timeout: float = 120) -> str:
    done = ran(["git", *args], timeout, cwd=path)
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
    kinds = {kind: table[kind] for kind in KINDS if kind in table}   # each declared kind ships in every release
    if not kinds:
        raise ValueError("`[release.android]` 도 `[release.desktop]` 도 없다 — 무엇을 빌드할지 정하지 않았다")
    wrong = [k for k in ("version_source",) if not _text(table.get(k))] + \
            [kind for kind, cfg in kinds.items() if not isinstance(cfg, dict)]
    wrong += [f"{kind}.{k}" for kind, cfg in kinds.items() if isinstance(cfg, dict)
              for k in KINDS[kind] if not _text(cfg.get(k))]
    if wrong:
        raise ValueError(f"`[release]` 의 글줄 칸이 비었거나 글줄이 아니다: {', '.join(wrong)}")
    return {"version_source": table["version_source"], **{kind: {k: cfg[k] for k in KINDS[kind]}
                                                          for kind, cfg in kinds.items()}}


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def seed(repo: Path, sha: str, source: str) -> str:
    """The first release's version: the one file `version_source` names, read at the pinned commit."""

    text = git(repo, "show", f"{sha}:{source}")
    try:
        if not source.endswith((".json", ".toml")):
            raise ValueError(f"`{source}` 는 .json 도 .toml 도 아니다")
        data = json.loads(text) if source.endswith(".json") else tomllib.loads(text)
        table = data.get("package") if isinstance(data, dict) and source.endswith(".toml") else None
        found = table["version"] if isinstance(table, dict) and table.get("version") else \
            data.get("version") if isinstance(data, dict) else None
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"`{source}` 에서 버전을 읽지 못했다 — {exc}") from exc
    if not isinstance(found, str) or not SEMVER.fullmatch(found):
        raise ValueError(f"`{source}` 의 버전 {found!r} 은 major.minor.patch 가 아니다")
    return found


# -- Which version a press publishes --------------------------------------------------------------

def parsed(tag: str) -> tuple[int, int, int] | None:
    found = re.fullmatch(r"v" + SEMVER.pattern, tag, re.ASCII)
    return tuple(map(int, found.groups())) if found else None


def version(releases: list[dict], taken: list[str], bump: str | None, first: str, kinds=("android",)) -> str:
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
    if "android" in kinds:   # a version a declared kind cannot carry is refused here, before the card shows it
        code(release)
    if "desktop" in kinds:
        release_desktop.carries(release)
    return release


def accepted(shown: str, releases: list[dict], taken: list[str], bump: str | None, first: str, kinds=("android",)):
    """The version a press publishes, recomputed at pin time. A tag that appeared since the card was drawn
    changes it, and then the press is refused before anything is reserved or written."""

    now = version(releases, taken, bump, first, kinds)
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
            # A stop ended the fetch; the ref it may have written still goes, on a halt of its own that is never
            # set, so the owned and bounded runner still holds it.
            with stoppable(threading.Event()) if getattr(_stop, "halt", None) else nullcontext():
                git(repo, "update-ref", "-d", own, timeout=30)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
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


def owned(path: Path, under: Path | None = None) -> Path:
    """`path` when it lies strictly under `under` (`WORK` unless named) and no folder from the data root down
    to it is a link or a junction. Checked before a checkout or a key is written and before one is removed."""

    root, target = Path(os.path.abspath(WORK if under is None else under)), Path(os.path.abspath(path))
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

def tools(cfg: dict | None = None) -> dict:
    """The JDK, the SDK with the build tools, and the newest NDK, which an app with no native code (`abi = "none"`)
    does without, as the build and the checks use them. Nothing for settings that declare no Android kind."""

    if cfg is not None and "android" not in cfg:
        return {}

    homes = [os.environ.get("JAVA_HOME"), str(oneshot_android.ROOT / "jdk-17")]
    java = next((Path(h) for h in homes if h and oneshot_android.java(Path(h))), None)
    names = (f"aapt2{oneshot_android.EXE}", f"zipalign{oneshot_android.EXE}", f"apksigner{oneshot_android.BAT}")
    sdk = next((r for r in oneshot_android.roots()
                if all((r / "build-tools" / oneshot_android.BUILD_TOOLS / n).is_file() for n in names)), None)
    ndks = sorted((sdk / "ndk").iterdir()) if sdk and (sdk / "ndk").is_dir() else []
    gone = [what for what, have in (("JDK 17 (JAVA_HOME)", java), (f"build-tools {oneshot_android.BUILD_TOOLS}", sdk),
                                    ("NDK", ndks or cfg is not None and cfg["android"]["abi"] == "none")) if not have]
    if gone:
        raise ValueError(f"Android 배포 도구가 없다: {', '.join(gone)}")
    bin_ = sdk / "build-tools" / oneshot_android.BUILD_TOOLS
    return {"java": java, "sdk": sdk, "ndk": ndks[-1] if ndks else None, "aapt2": bin_ / names[0], "zipalign": bin_ / names[1],
            "apksigner": bin_ / names[2], "keytool": java / "bin" / f"keytool{oneshot_android.EXE}"}


SECRET = "RELEASE_KEY_PASSWORD"   # reaches the key tools only, set by `keyed`; never inherited
UNSHARED = {SECRET, *host.TOKENS}
# The English not-found messages of PowerShell, cmd and sh, each naming the program as `n`. The machine's own,
# in whatever language it speaks, are learned from its shells (`learned_shapes`).
ENGLISH_SHAPES = tuple(re.compile(shape, re.IGNORECASE | re.MULTILINE) for shape in (
    r"The term '(?P<n>[^']+)' is not recognized as (?:the|a) name of a cmdlet",
    r"'(?P<n>[^']+)' is not recognized as an internal or external command",
    r"(?:^|:\s)(?P<n>[^\s:/\\]+): (?:command )?not found"))
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LAST_LINES = 12   # where a failed command's own diagnostic sits in its log
NAME = r"(?P<n>[^\s'\"/\\:]+)"
AFTER_NAME = 16   # of a message's text past the name: enough to tell it from a source error, short of any wrap
# What the gate's shell runs to ask: the program itself (in the PowerShell the resolver chose, 5.1 or 7), then the
# shells a build script may start in turn.
# ponytail: a second PowerShell a build script starts itself words its message apart from the gate's and is not
# learned; probe it through a catalogued `shell` entry if a build turns out to do that
PROBES = ("{name}", "cmd /c {name}", "sh -c {name}")


def shapes_from(name: str, out: str) -> list[re.Pattern]:
    """The not-found message in `out`, a shell's answer for `name` (which exists nowhere), as a pattern naming the
    program as `n`: from the name's first mention, with the quote before it, to `AFTER_NAME` characters past its
    last, since PowerShell wraps the rest at the console's width. The name starts after a quote, a space, a colon
    or the line's start, so the last part of a path (`'./tool/x.cmd'`) is never taken for a bare name. A line that
    only echoes the command is not a message."""

    shapes = []
    for line in ANSI.sub("", out).splitlines():
        start = line.find(name)
        start -= start > 0 and line[start - 1] in "'\""
        said = line[start:line.rfind(name) + len(name) + AFTER_NAME].rstrip() if name in line else ""
        if len(said.replace(name, "").strip(" '\"")) >= 8 and "{" not in said:
            escaped = re.escape(said).replace(re.escape(name), NAME, 1).replace(re.escape(name), "(?P=n)")
            shapes.append(re.compile(("" if said[0] in "'\"" else r"(?<![^\s:])") + escaped, re.MULTILINE))
    return shapes[:1]


_learned: tuple[re.Pattern, ...] | None = None   # kept only once every shell has answered


def learned_shapes(halt: threading.Event, env: dict) -> tuple[re.Pattern, ...]:
    """What this machine's shells print for a program they cannot find, asked through the gate itself, so the shell,
    its language and the decoding are a build's own. They run in the build's own environment, without its secrets,
    and stop with it. Best effort: a failure teaches nothing, and only a message from every shell is kept."""

    # ponytail: a machine without a shell (no `sh`) asks again each failed build; remember an absent shell if slow
    global _learned
    if _learned is not None:
        return _learned
    name, shapes, whole = f"wikiagentmissing{secrets.token_hex(4)}", [], True
    try:
        with tempfile.TemporaryDirectory() as cwd:
            for probe in PROBES:
                if halt.is_set():
                    return tuple(shapes)
                _, out, cut = specs.gate(probe.format(name=name), Path(cwd), halt, env=env, timeout=60, idle=30)
                said = shapes_from(name, out)
                whole = whole and not cut and bool(said)
                shapes += said
    except Exception:  # noqa: BLE001 — a shell that cannot be asked teaches nothing; the build's own error stands
        return tuple(shapes)
    if whole and not halt.is_set():
        _learned = tuple(shapes)
    return tuple(shapes)


def environment(kit: dict) -> dict:
    """The tools' environment, without any key password or GitHub token the server itself was started with: the
    adapter's build runs here, and what it prints can reach the repair agent."""

    shared = {k: v for k, v in os.environ.items() if k.upper() not in UNSHARED | ({"NDK_HOME"} if kit else set())}
    return shared if not kit else {**shared, "JAVA_HOME": str(kit["java"]), "ANDROID_HOME": str(kit["sdk"]),
                                   **({"NDK_HOME": str(kit["ndk"])} if kit.get("ndk") else {}),
                                   "PATH": os.pathsep.join([str(kit["java"] / "bin"), os.environ.get("PATH", "")])}


def lacks_tool(out: str, env: dict, halt: threading.Event) -> bool:
    """The build failed because this machine lacks a program: its shell's not-found message, English or the
    machine's own, for a bare name that is not on the build's `PATH`, among the log's last lines. A path is the
    repository's own script, which a source change can restore. An earlier optional probe is not why the build
    failed, and a source error that merely quotes a name is not a shell's message."""

    # ponytail: a not-found message in the last lines beside an unrelated error still reads as the machine's
    tail = "\n".join([line for line in ANSI.sub("", out).splitlines() if line.strip()][-LAST_LINES:])
    names = (m["n"] for shape in (*ENGLISH_SHAPES, *learned_shapes(halt, env)) for m in shape.finditer(tail))
    return any(not re.search(r"[/\\]|^\.", name) and shutil.which(name, path=env.get("PATH")) is None
               for name in names)


def keyed(kit: dict, password: str) -> dict:
    return {**environment(kit), SECRET: password}


def output(path: Path, kind: dict, release: str, key: str = "artifact") -> Path:
    """`kind[key]` in the checkout `path`, its `{version}` filled in; one outside the checkout is refused."""

    named = kind[key].replace("{version}", release)
    file = (path / named).resolve()
    if not file.is_relative_to(path.resolve()):
        raise ValueError(f"`{named}` 가 시도의 체크아웃 밖을 가리킨다")
    return file


def build(path: Path, android: dict, release: str, kit: dict, halt: threading.Event, read=None) -> Path:
    """The adapter's build command of one kind (`android`, or `desktop` with `read`) in the attempt's checkout,
    given `RELEASE_VERSION`, Android's `RELEASE_VERSION_CODE` and `RELEASE_TAURI_CONFIG`, an overlay beside the
    checkout a Tauri build passes as one more `-c` (`TAURI_CONFIG` cannot: the CLI replaces it with its own `-c`
    values). No source file is edited. `read(file)` refuses an artifact its kind does not make; else it is an APK."""

    made = [k for k in ("artifact", "exe") if k in android]
    for k in made:   # a file the commit carries is not this build's output
        if output(path, android, release, k).exists():
            raise ValueError(f"`{android[k]}` 가 빌드 전부터 체크아웃에 있다 — 빌드가 만든 파일만 검사한다")
    overlay = path.parent / "tauri.release.json"
    # Android's only. Off, or a target's `autoIncrementVersionCode` would replace the code the version maps to.
    codes = {} if "exe" in android else {"versionCode": code(release), "autoIncrementVersionCode": False}
    overlay.write_text(json.dumps({"version": release, "bundle": {"android": codes} if codes else {}}), encoding="utf-8")
    env = {**environment(kit), "RELEASE_VERSION": release, "RELEASE_TAURI_CONFIG": str(overlay),
           **({"RELEASE_VERSION_CODE": str(codes["versionCode"])} if codes else {})}
    exited, out, cut = specs.gate(android["build"], path, halt, env=env, timeout=BUILD_SECONDS, idle=QUIET_SECONDS)
    if exited != 0:
        tail = errorlog.redact("\n".join(out.splitlines()[-40:]))
        said = f"빌드 실패 — {cut or f'{exited} 로 끝났다'}\n{tail}"
        # A person's stop, a deadline or silence, and a missing tool say nothing about the source.
        # The halt again after the shells were asked: a stop while they answered is still a stop.
        machine = halt.is_set() or cut or lacks_tool(out, env, halt) or halt.is_set()
        raise ValueError(said) if machine else BuildFailed(said)
    for k in made:
        if not output(path, android, release, k).is_file():
            raise BuildFailed(f"빌드가 `{android[k]}` 를 만들지 않았다")
    apk = output(path, android, release)
    try:   # read before signing, so a broken product is the build's failure and not the signing's
        read(apk) if read else badging(run([kit["aapt2"], "dump", "badging", apk], environment(kit)))
    except ValueError as exc:
        raise BuildFailed(f"빌드가 만든 `{android['artifact']}` 를 {'설치 파일' if read else 'APK'} 로 읽지 못했다 "
                          f"— {exc}") from exc
    return apk


class BuildFailed(ValueError):
    """The adapter's build command failed, or made no artifact: something in the repository needs changing."""


def run(argv: list, env: dict | None = None, timeout: float = 300) -> str:
    done = ran([str(a) for a in argv], timeout, env=env)
    if done.returncode:
        said = (done.stderr or done.stdout).strip()
        if env and env.get(SECRET):   # a key tool's diagnostic never carries its password onward
            said = said.replace(env[SECRET], "***")
        raise ValueError(f"{Path(str(argv[0])).name} 실패 — {said[-400:]}")
    return done.stdout


def test_key(store: Path, kit: dict) -> dict:
    """A disposable key for a rehearsal at `store`, valid for one day. The caller removes `store` whatever
    happens, because `keytool` can write it and still fail."""

    return generated(store, "rehearsal", 1, "CN=wiki-agent release rehearsal", kit)


def generated(store: Path, alias: str, days: int, name: str, kit: dict) -> dict:
    """A new RSA key at `store` with a random password, which reaches `keytool` only through its environment."""

    password = secrets.token_urlsafe(24)
    run([kit["keytool"], "-genkeypair", "-keystore", store, "-alias", alias, "-keyalg", "RSA",
         "-keysize", "2048", "-validity", str(days), "-dname", name,
         "-storepass:env", SECRET, "-keypass:env", SECRET], keyed(kit, password))
    return {"keystore": store, "alias": alias, "password": password}


def sign(apk: Path, out: Path, key: dict, kit: dict) -> Path:
    """Align, then sign with `key`. The password reaches the key tools only through their environment, never
    their command line and never the build."""

    aligned = out.with_suffix(".aligned.apk")
    run([kit["zipalign"], "-p", "-f", "4", apk, aligned], environment(kit))
    run([kit["apksigner"], "sign", "--ks", key["keystore"], "--ks-key-alias", key["alias"],
         "--ks-pass", f"env:{SECRET}", "--out", out, aligned], keyed(kit, key["password"]))
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

    return built_wrong(seen, release, android, previous) + signed_wrong(seen, cert, previous)


def built_wrong(seen: dict, release: str, android: dict, previous: dict | None = None) -> list[str]:
    """What the build itself got wrong: a source change can fix each of these."""

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
    native = [] if android["abi"] == "none" else [android["abi"]]   # `none`: an app with no native code at all
    for name, want in (("설정", native), *((("이전 정식 APK", previous["abis"]),) if previous else ())):
        if seen["abis"] != want:
            out.append(f"ABI {seen['abis']} 가 {name}의 {want} 와 다르다 — 기존 기기가 업데이트를 받지 못한다")
    if seen["debuggable"]:
        out.append("디버그 빌드다 — 정식 릴리스는 debuggable 이 아니다")
    return out


def signed_wrong(seen: dict, cert: str, previous: dict | None = None) -> list[str]:
    """A certificate other than the key's: the release key's problem, never the source's."""

    out = []
    for name, want in (("기대한 인증서", [cert]), *((("이전 정식 APK 의 인증서", previous["certs"]),) if previous else ())):
        if seen["certs"] != want:
            out.append(f"서명 인증서 {seen['certs']} 가 {name} {want} 와 다르다 — 설치된 앱이 업데이트되지 않는다")
    return out


# -- The release key (step 2) ---------------------------------------------------------------------
# One key per repository signs every production APK it ever ships, so Android installs each one as an update of
# the last. The keystore lives under `KEYS`, outside every checkout. Its password lives only in this Windows
# account's Credential Manager. Neither is handed to the build command or the repair agent. They run as the same
# Windows account, though, which can read that account's vault and profile, so the boundary is the account. Code
# that is not trusted must run under another account before step 5 enables publishing. The certificate file is
# written last and marks the key as established. After that, a missing or changed half is refused and never
# replaced, because a new key would strand every installed copy.

# Never under a checkout: unpackaged, `paths.DATA` is the checkout, and a key removed with a worktree is lost for
# good. Packaged, `KEYS` sits inside `paths.DATA`, which is then this same LocalAppData folder.
KEYS = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "wiki-agent-data" / "release-keys"
KEY_DAYS = 10000   # about 27 years; Google Play wants a key valid past 2033
FIRST_RELEASE_NOTE = ("첫 정식 릴리스입니다. 디버그 APK 를 설치했던 기기는 서명이 달라 이 버전으로 업데이트되지 "
                      "않습니다. 디버그 앱을 한 번 지우고 이 APK 를 설치하세요. 디버그 앱에 저장된 데이터는 함께 지워집니다.")


def remote(repo: Path) -> str:
    """The canonical remote, `github.com/<owner>/<repo>`, that names the repository's key and ledger."""

    url = git(repo, "remote", "get-url", "origin").strip()
    found = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                         r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url)
    if not found or {found.group(1), found.group(2)} & {".", ".."}:
        raise ValueError("origin 이 GitHub 저장소 주소가 아니다")   # never echoed: a URL can carry a token
    return f"github.com/{found.group(1)}/{found.group(2)}".lower()


def target(name: str) -> str:
    return f"wiki-agent release key {name}"


class _Credential(ctypes.Structure):   # CREDENTIALW
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.c_void_p),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _advapi():
    if os.name != "nt":
        raise ValueError("정식 서명 키의 비밀번호는 Windows 자격 증명 관리자에만 둔다")
    return ctypes, ctypes.WinDLL("advapi32", use_last_error=True)


GENERIC, LOCAL_MACHINE, NOT_FOUND = 1, 2, 1168


def secret_read(name: str) -> str | None:
    ctypes, a = _advapi()
    found = ctypes.POINTER(_Credential)()
    if not a.CredReadW(target(name), GENERIC, 0, ctypes.byref(found)):
        if ctypes.get_last_error() == NOT_FOUND:
            return None
        raise OSError(f"자격 증명을 읽지 못했다: {ctypes.get_last_error()}")
    try:
        blob = found.contents
        return ctypes.string_at(blob.CredentialBlob, blob.CredentialBlobSize).decode("utf-16-le")
    finally:
        a.CredFree(found)


def secret_write(name: str, value: str) -> None:
    ctypes, a = _advapi()
    data = value.encode("utf-16-le")
    blob = ctypes.create_string_buffer(data, len(data))
    cred = _Credential(Type=GENERIC, TargetName=target(name), CredentialBlobSize=len(data),
                       CredentialBlob=ctypes.cast(blob, ctypes.c_void_p), Persist=LOCAL_MACHINE,
                       UserName="wiki-agent")
    if not a.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f"자격 증명을 쓰지 못했다: {ctypes.get_last_error()}")


def secret_delete(name: str) -> None:
    ctypes, a = _advapi()
    if not a.CredDeleteW(target(name), GENERIC, 0) and ctypes.get_last_error() != NOT_FOUND:
        raise OSError(f"자격 증명을 지우지 못했다: {ctypes.get_last_error()}")


LOCK_SECONDS = 60


def plain(path: Path) -> Path:
    """`path` when nothing on the way to it, the leaf included and even a dangling one, is a link or junction."""

    if os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.abspath(path)) or \
            Path(path).is_symlink() or is_junction(Path(path)):
        raise ValueError(f"{path} 는 링크를 거친다 — 서명 키는 링크를 따라가지 않는다")
    return Path(os.path.abspath(path))


@contextmanager
def held(folder: Path):
    """One process at a time per repository's key, released when the holder dies. Two creations at once
    would otherwise leave two passwords and two keystores, and only one survives."""

    lock, deadline = plain(folder / "lock"), time.monotonic() + LOCK_SECONDS
    while True:
        try:
            with verify.locked(lock):   # the final gate's lock: fails at once, so the wait is here
                yield
            return
        except verify.Pending:
            if time.monotonic() > deadline:
                raise ValueError(f"{folder} 의 서명 키를 다른 요청이 쓰고 있다 — 끝난 뒤 다시 하라") from None
            time.sleep(0.2)


def opens(store: Path, password: str | None, kit: dict) -> str | None:
    """`store`'s certificate SHA-256 when `password` opens it, else None."""

    if password is None or not store.is_file():
        return None
    try:
        return certificate(store, "release", password, kit)
    except ValueError:
        return None


def release_key(repo: Path, kit: dict, create: bool = False, name: str | None = None) -> dict:
    """The repository's one production key with its certificate's SHA-256. `create` makes it when it was never
    established, or finishes a creation that stopped. Nothing here deletes or overwrites a keystore or a password
    that might be the repository's: an established key that lost a half or changed, a keystore no password opens,
    and a surviving password with no keystore are each refused for a person to settle. `name`, when given, is
    the canonical remote a caller already checked, so an origin that changes meanwhile never picks the key."""

    name = name or remote(repo)
    keys = Path(os.path.realpath(KEYS))   # the target's checkout, or wiki-agent's own (unpackaged, that is the data)
    if any(keys.is_relative_to(Path(os.path.realpath(root))) for root in (repo, paths.RESOURCES)):
        raise ValueError(f"{KEYS} 가 체크아웃 안이다 — 서명 키는 모든 체크아웃 밖에만 둔다")
    folder = plain(owned(KEYS / name, KEYS))
    folder.mkdir(parents=True, exist_ok=True)
    store, marker, pending = (plain(folder / n) for n in ("release.jks", "certificate.sha256", "release.jks.new"))
    with held(folder):   # every check below is made again under the lock, never trusted from before it
        password = secret_read(name)
        if marker.exists():
            cert = marker.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", cert):
                raise ValueError(f"{marker} 가 깨졌다 — 인증서 기록을 백업에서 되살려라. 키는 그대로 두었다")
            if not store.is_file() or password is None:
                gone = "키 파일" if not store.is_file() else "자격 증명 관리자의 비밀번호"
                raise ValueError(f"{name} 의 정식 서명 키가 있었는데 {gone}이 없다 — 새 키로 서명하면 설치된 앱이 "
                                 f"업데이트되지 않는다. 백업에서 되살려라: {store}")
            if opens(store, password, kit) != cert:
                raise ValueError(f"{store} 의 인증서가 기록한 {cert[:16]}… 와 다르다 — 바뀐 키로는 서명하지 않는다")
            return {"keystore": store, "alias": "release", "password": password, "cert": cert, "name": name}
        if store.exists():   # stopped between its rename and its record, or a record someone removed
            if (cert := opens(store, password, kit)) is None:
                raise ValueError(f"{store} 가 있는데 인증서 기록도, 그 키를 여는 비밀번호도 없다 — 이 키로 서명한 적이 "
                                 "있는지 사람이 확인해야 한다. 키는 그대로 두었다")
        elif password is not None:   # stopped after its password was stored, or its keystore was lost
            if (cert := opens(pending, password, kit)) is None:
                raise ValueError(f"자격 증명 관리자에 '{target(name)}' 가 남아 있는데 키 파일이 없다 — 백업한 "
                                 f"{store.name} 을 되살리거나, 그 키를 버릴 거라면 그 자격 증명을 직접 지운 뒤 다시 만든다")
        elif pending.exists():   # a key that stopped before its password was stored, or a copy put back by hand
            raise ValueError(f"{pending} 가 있는데 그 키를 여는 비밀번호가 자격 증명 관리자에 없다 — 되살리는 중이면 "
                             "비밀번호를 되살리고, 아니면 이 파일을 지운 뒤 다시 만든다. 키는 그대로 두었다")
        else:
            cert = None
        if not create:
            raise ValueError(f"{name} 의 정식 서명 키가 {'만들다 멈췄다' if cert else '아직 없다'} — --create 로 "
                             f"{'마저 만든다' if cert else '한 번 만든다'}")
        if cert is None:   # nothing stored anywhere, not even a staged keystore
            password = generated(pending, "release", KEY_DAYS, f"CN={name}", kit)["password"]
            secret_write(name, password)
            cert = certificate(pending, "release", password, kit)
        if not store.exists():   # a keystore already there is the one `cert` came from; never overwrite it
            pending.replace(store)
        replaced(marker, cert + "\n")   # last, and whole: from here on the key is the repository's
        return {"keystore": store, "alias": "release", "password": password, "cert": cert, "name": name}


def replaced(target: Path, text: str) -> None:
    """`target` replaced whole by `text`, through a fresh staging file beside it. The exclusive open never writes
    through an existing name or its links. A failure removes only the staging file this call made, and only
    while that name still holds the same file. The original error is raised; a failed cleanup is recorded."""

    staged = target.with_name(f"{target.name}.{secrets.token_hex(4)}.new")
    made = None
    try:
        with open(staged, "x", encoding="utf-8") as out:
            made = os.fstat(out.fileno())
            out.write(text)
        staged.replace(target)
    except BaseException:
        if made is not None:
            try:
                now = os.stat(staged)
                if (now.st_dev, now.st_ino) == (made.st_dev, made.st_ino):   # still ours: never one swapped in
                    staged.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errorlog.record("release-staging", exc, path=str(staged))
        raise


def handed(repo: Path, record: dict, repair, halt: threading.Event) -> dict:
    """`record["repair"]` with what `repair(repo, record)` did: the task it started, or why none runs. The
    attempt has ended either way, so a refusal is recorded, never raised. A task whose spec was saved but whose
    start was refused (`release_repair.Started`) is still named. A release stopped before this starts none."""

    if halt.is_set():
        return {**record["repair"], "error": "멈춤 — 사람이 배포를 멈춰서 수정 작업을 시작하지 않았다"}
    if repair is None:
        return record["repair"]
    try:
        return {**record["repair"], "task": repair(repo, record)}
    except Exception as exc:   # noqa: BLE001 — the attempt is over; the card shows why no repair runs
        return {**record["repair"], **({"task": exc.task} if getattr(exc, "task", None) else {}),
                "error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 2 or argv[0] not in ("rehearse", "key"):
        print("usage: rehearse <repo> [--ref <ref>] | key <repo> [--create]", file=sys.stderr)
        return 2
    if argv[0] == "key":   # the password is never printed; it stays in Credential Manager
        key = release_key(Path(argv[1]).resolve(), tools(), create="--create" in argv)
        print(json.dumps({"remote": key["name"], "keystore": str(key["keystore"]), "cert": key["cert"],
                          "backup": f"{key['keystore']} 와 자격 증명 관리자의 '{target(key['name'])}' 를 함께 "
                                    "백업하라. 둘 중 하나라도 잃으면 설치된 앱을 업데이트할 수 없다."},
                         ensure_ascii=False, indent=2))
        return 0
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else "main"
    from .release_rehearsal import rehearse   # it builds on this module
    result = rehearse(Path(argv[1]).resolve(), ref)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
