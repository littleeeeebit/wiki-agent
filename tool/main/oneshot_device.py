"""oneshot S7 for an Android app: the debug APK on an emulator the run owns, every screen shot in every state,
the mechanical and placement findings read off the native hierarchy, and the scenarios run as instrumented
tests. A server routine, never a model.

`docs/plans/oneshot/7-android.md` ("S7 — the emulator loop", "How 7e builds it") is the design. The build
turn fixes the conventions it relies on: the debug build opens a screen in a state when started with the
extras `oneshot.screen` and `oneshot.state`, each feature's control is exported to the hierarchy as
`feature:<id>`, and the turn's `oneshot-device` block names the APKs, the package and the launch activity.
The emulator is `android_owned`'s, cleaned on every exit; a placement or mechanical finding is high.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from xml.etree import ElementTree

import android_owned
from common import is_junction

from . import oneshot, oneshot_android
from .oneshot_profile import DEVICES
from .oneshot_screens import MOCK_PATH

BLOCK = "oneshot-device"
STATES = ("empty", "full", "loading", "error")
VIEW = "phone"
SETTLE_SECONDS = 1.5
TARGET_DP = 48
MIN_CONTRAST = 3.0
MAX_TAPS = 15
INSTALL_SECONDS = 300
TESTS_SECONDS = 1800
DUMP = "/data/local/tmp/oneshot-window.xml"
SYSTEM_UI = "com.android.systemui"   # draws the shade, so an app's notification views carry its package
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
ACTIVITY = re.compile(r"\.?[A-Za-z][A-Za-z0-9_$]*(\.[A-Za-z][A-Za-z0-9_$]*)*")
RUNNER = re.compile(PACKAGE.pattern + r"/" + ACTIVITY.pattern)
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
GRANTS = re.compile(r"android\.permission\.[A-Z_]+")
# A capability that reads another app's screen: its scenarios run against the fixture viewer.
READS = re.compile(r"MEDIA_PROJECTION|MediaProjection|BIND_ACCESSIBILITY_SERVICE")


def never() -> bool:
    return False


def finding(sid: str, state: str, what: str) -> dict:
    return {"severity": "high", "screen": sid, "viewport": VIEW, "state": state, "what": what}


def halted(stopped) -> None:
    if stopped():
        raise ValueError("에뮬레이터 검사 중에 멈췄다 — [재개] 로 빌드부터 다시 검사한다")


def reads_screen(spec: dict) -> bool:
    return any(READS.search(r["capability"]["permission"]) for r in spec.get("requirements") or [] if r.get("capability"))


def grants(spec: dict) -> list[str]:
    """The permissions the approved requirements state, and only those: what the run grants the app."""

    return sorted({p for r in spec.get("requirements") or [] if r.get("capability")
                   for p in GRANTS.findall(r["capability"]["permission"])})


def scenarios(spec: dict) -> list[str]:
    """The core scenarios' requirement ids: each has an instrumented test `scenario_<id>_…`."""

    return [r["id"] for r in spec.get("requirements") or [] if r.get("postconditions")]


def owed(spec: dict) -> list[dict]:
    """The checks the backend review left for the phone (`oneshot_review.owing`): each has an instrumented
    test `owed_<id>_…`, and a look passes only with every one of them run and passing on its HEAD."""

    return [x for x in spec["oneshot"].get("owed") or [] if x["kind"] == "android"]


# -- The build turn's block ----------------------------------------------------------------------------

def _apk(tree: Path, value, where: str) -> str:
    if not isinstance(value, str) or not value.endswith(".apk") or not MOCK_PATH.fullmatch(value) \
            or {".", "..", ".git"} & {part.lower() for part in value.split("/")}:
        raise ValueError(f"`{BLOCK}` 의 {where} 는 작업트리 안의 상대 경로(`/` 로 나눔)로 적은 `.apk` 다")
    return value


def suffix(o: dict) -> str:
    """The run's application id suffix: every package the run installs carries it, so on the owner's phone
    none of them is an app already there."""

    return f".oneshot{o['run']}" if o.get("run") else ""


def declared(tree: Path, final: str, needs_fixture: bool, ending: str = "") -> dict:
    """The build turn's `oneshot-device` block, checked for shape: the APK the gate builds, its package and
    launch activity, the instrumented test APK and its runner, and the fixture viewer when the app reads
    another app's screen, each package ending with the run's suffix `ending`. Whether the APKs exist is
    `built`'s, after the gate."""

    value = oneshot.block(final, BLOCK)
    if not isinstance(value, dict) or not isinstance(value.get("tests"), dict):
        raise ValueError(f"`{BLOCK}` 는 apk, package, activity, tests{{apk, runner}} 를 담은 객체다")
    out = {"apk": _apk(tree, value.get("apk"), "apk"), "package": value.get("package"),
           "activity": value.get("activity"), "tests": {"apk": _apk(tree, value["tests"].get("apk"), "tests.apk"),
                                                        "runner": value["tests"].get("runner")}}
    if not isinstance(out["package"], str) or not PACKAGE.fullmatch(out["package"]):
        raise ValueError(f"`{BLOCK}` 의 package 는 앱의 applicationId 다 (예: com.example.app)")
    if not isinstance(out["activity"], str) or not ACTIVITY.fullmatch(out["activity"]):
        raise ValueError(f"`{BLOCK}` 의 activity 는 띄울 액티비티다 (예: .MainActivity)")
    if not isinstance(out["tests"]["runner"], str) or not RUNNER.fullmatch(out["tests"]["runner"]):
        raise ValueError(f"`{BLOCK}` 의 tests.runner 는 `<테스트 패키지>/<러너 클래스>` 다")
    fixture = value.get("fixture")
    if needs_fixture:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("package"), str) \
                or not PACKAGE.fullmatch(fixture["package"]) or fixture["package"] == out["package"]:
            raise ValueError(f"다른 앱의 화면을 읽는 앱이라 `{BLOCK}` 에 fixture{{apk, package}} (고정 장면 뷰어)가 있어야 한다")
        out["fixture"] = {"apk": _apk(tree, fixture.get("apk"), "fixture.apk"), "package": fixture["package"]}
    if ending:
        tests = out["tests"]["runner"].split("/")[0]
        if not out["package"].endswith(ending) or not tests.startswith(out["package"] + ".") \
                or ("fixture" in out and not out["fixture"]["package"].endswith(ending)):
            raise ValueError(f"`{BLOCK}` 의 패키지가 이 실행의 것이 아니다 — 앱과 fixture 의 applicationId 는 `{ending}` 로 "
                             "끝나고, 테스트 패키지는 앱의 것에 이어진다")
        if out["activity"].startswith("."):   # it would resolve under the suffixed package, where no class is
            raise ValueError(f"`{BLOCK}` 의 activity 는 클래스의 전체 이름이다 — 접미사는 패키지만 바꾼다")
    return out


def inside(tree: Path, apk: str) -> bool:
    """`apk` is reached through plain folders of the tree: no link or junction on the way, nowhere outside it."""
    parts = Path(apk).parts
    steps = [tree.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    return not any(p.is_symlink() or is_junction(p) for p in steps) and (tree / apk).resolve().is_relative_to(tree.resolve())


def built(tree: Path, device: dict) -> str:
    """Why an APK the block names is not a plain file in the tree after the gate, or empty."""

    for apk in [device["apk"], device["tests"]["apk"], *([device["fixture"]["apk"]] if "fixture" in device else [])]:
        if not inside(tree, apk) or not (tree / apk).is_file():
            return f"게이트 뒤에 `{apk}` 가 작업트리 안의 파일로 없다 — `gate_cmd` 가 그 APK 를 빌드해야 한다"
    return ""


def cleared(tree: Path, device: dict) -> str:
    """Before the final gate: the app's APK an earlier build left is removed, so the one frozen after is the gate's
    own, built with the values the gate carries. Refused, touching nothing, when Git cannot say whether it tracks
    it, when it does (a build output is never committed), or when the way to it is not plain folders of the tree."""

    import subprocess

    apk = device["apk"]
    if not inside(tree, apk):
        return f"`{apk}` 로 가는 길에 링크나 작업트리 밖이 있다 — 작업트리 안의 보통 폴더에 빌드한다"
    # Every entry Git tracks, compared casefolded with the declared spelling and the one the file resolves to:
    # Windows opens a file by any casing, and Git's pathspec matches only its own.
    def git(*args: str) -> str | None:   # None: the read failed
        done = subprocess.run(["git", *args], cwd=tree, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        return None if done.returncode else done.stdout

    from . import specs   # it imports this module

    # A clean tree first, read as the final gate reads it (`specs.status`: the app's own records are not work): a
    # staged removal or a local edit, and nothing is deleted under it. Then HEAD and the index.
    status, index, head = specs.status(tree), git("ls-files", "-z"), git("ls-tree", "-r", "-z", "--name-only", "HEAD")
    if index is None or head is None:
        return f"`{apk}` 를 Git 이 추적하는지 읽지 못했다 — 최종 게이트를 시작하지 않는다"
    if status.returncode or status.stdout.strip():
        return "커밋 안 된 변경이 있다 — 최종 게이트 전에 커밋한다"
    names = {apk.casefold(), (tree / apk).resolve().relative_to(tree.resolve()).as_posix().casefold()}
    if names & {e.casefold() for e in (index + "\0" + head).split("\0") if e}:
        return f"`{apk}` 가 커밋돼 있다 — 빌드 결과는 커밋하지 않는다. 지우고 무시 목록에 넣으면 게이트가 새로 만든다"
    try:
        (tree / apk).unlink(missing_ok=True)
    except OSError as exc:
        return f"이전 빌드가 남긴 `{apk}` 를 지우지 못했다 ({type(exc).__name__})"
    return ""


# -- The device ----------------------------------------------------------------------------------------

def tools() -> dict:
    """The card's toolchain as `android_owned` takes it: each tool's path with its digest now."""

    found = oneshot_android.status()
    if found["missing"]:
        raise ValueError(oneshot_android.missing()[0])
    sdk = Path(found["sdk"])
    adb, emulator = sdk / "platform-tools" / f"adb{oneshot_android.EXE}", sdk / "emulator" / f"emulator{oneshot_android.EXE}"
    image = sdk / next(folder for key, _, folder, _ in oneshot_android.PIECES if key == "image")
    return {"adb": {"path": str(adb), "sha256": android_owned.sha256(adb)},
            "emulator": {"path": str(emulator), "sha256": android_owned.sha256(emulator)},
            "image": {"path": str(image), "sha256": android_owned.image_digest(image)}}


def cleaned(base: Path) -> None:
    """Every earlier look's emulator cleaned from its record before a new one boots: a server that died
    mid-look left it. What cannot be cleaned stops the look, naming it."""

    for folder in sorted(base.glob("r*")) if base.is_dir() else ():
        if left := android_owned.cleanup(folder):
            raise ValueError(f"이전 회차의 에뮬레이터 정리가 끝나지 않았다 ({folder.name}): {', '.join(left)}")


class Device:
    """The owned emulator as the look drives it: every command through `android_owned.call`."""

    def __init__(self, folder: Path, device: dict, size: dict, tree: Path):
        self.folder, self.d, self.size, self.tree, self.since = folder, device, size, tree, ""

    def adb(self, *args, timeout: float = android_owned.CALL_SECONDS, binary: bool = False):
        return android_owned.call(self.folder, list(args), timeout=timeout, binary=binary)

    def install(self, apk: str, package: str, done=lambda: None) -> None:
        """`done` runs once the install command succeeded: with `-R`, the proof the installation is this one."""

        # `-R`: the package manager replaces an existing installation unless told not to (`-r` is ignored), so
        # an installation already there, the run's or another's, fails the install instead.
        self.adb("install", "-R", "-t", str((self.tree / apk).resolve()), timeout=INSTALL_SECONDS)
        done()
        if not self.adb("shell", "pm", "path", package).strip().startswith("package:"):
            raise ValueError(f"`{apk}` 를 설치했는데 `{package}` 가 기기에 없다 — `{BLOCK}` 의 package 가 그 APK 의 것이어야 한다")

    def grant(self, permissions: list[str], projection: bool) -> None:
        """The stated permissions only. An install-time one cannot be granted and is already held."""

        for permission in permissions:
            try:
                if permission.endswith(".SYSTEM_ALERT_WINDOW"):
                    self.adb("shell", "appops", "set", self.d["package"], "SYSTEM_ALERT_WINDOW", "allow")
                else:
                    self.adb("shell", "pm", "grant", self.d["package"], permission)
            except ValueError:
                pass
        if projection:   # the screen-capture consent, as pressing "start now" once would give it
            try:
                self.adb("shell", "appops", "set", self.d["package"], "PROJECT_MEDIA", "allow")
            except ValueError:
                pass

    def open(self, screen: dict, state: str) -> str:
        """The screen in the state, drawn by the debug build; empty, or why it did not open."""

        # The device's clock, not a cleared log: on the owner's phone the log is not the run's to clear.
        self.since = self.adb("shell", "date", "+%s").strip()
        out = self.adb("shell", "am", "start", "-W", "-S", "-n", f"{self.d['package']}/{self.d['activity']}",
                       "--es", "oneshot.screen", screen["id"], "--es", "oneshot.state", state)
        # `Status: timeout` is a launch through a trampoline activity that outlived `-W`'s wait, not a failure.
        if "Error" in out:
            return f"앱을 열지 못했다 — {' '.join(out.split())[:200]}"
        time.sleep(SETTLE_SECONDS)
        if screen["surface"] == "overlay":   # drawn over another app: the fixture viewer, or the launcher
            if "fixture" in self.d:
                self.adb("shell", "monkey", "-p", self.d["fixture"]["package"], "-c", "android.intent.category.LAUNCHER", "1")
            else:
                self.adb("shell", "input", "keyevent", "KEYCODE_HOME")
        elif screen["surface"] == "notification":
            self.adb("shell", "cmd", "statusbar", "expand-notifications")
        time.sleep(SETTLE_SECONDS)
        return ""

    def close(self, screen: dict) -> None:
        if screen["surface"] == "notification":
            self.adb("shell", "cmd", "statusbar", "collapse")

    def hierarchy(self) -> ElementTree.Element:
        self.adb("shell", "uiautomator", "dump", DUMP)
        try:
            return ElementTree.fromstring(self.adb("exec-out", "cat", DUMP))
        except ElementTree.ParseError as exc:
            raise ValueError(f"화면 계층을 읽지 못했다 — {exc}") from exc

    def died(self) -> str:
        """A crash or ANR of the app since the last `open`, from the event log; empty when none."""

        since = ["-T", f"{self.since}.000"] if self.since.isdigit() else []
        lines = [line for line in self.adb("logcat", "-d", "-b", "events", *since).splitlines()
                 if ("am_crash" in line or "am_anr" in line) and self.d["package"] in line]
        return f"앱이 죽거나 응답하지 않았다 — {lines[0].strip()[:200]}" if lines else ""

    def tap(self, x: int, y: int) -> None:
        self.adb("shell", "input", "tap", str(x), str(y))
        time.sleep(SETTLE_SECONDS)


# -- Reading the hierarchy -----------------------------------------------------------------------------

def bounds(node: ElementTree.Element) -> tuple[int, int, int, int] | None:
    found = BOUNDS.fullmatch(node.get("bounds", ""))
    return tuple(map(int, found.groups())) if found else None


def feature(node: ElementTree.Element) -> str:
    """The feature a control is exported as: its resource id (a Compose test tag, a Flutter identifier), or
    the content description a notification's views carry."""

    for key in ("resource-id", "content-desc"):
        value = node.get(key, "")
        if value.startswith("feature:"):
            return value[len("feature:"):]
    return ""


def on_screen(node: ElementTree.Element, size: dict) -> bool:
    box = bounds(node)
    return bool(box) and node.get("visible-to-user", "true") == "true" and box[2] > box[0] and box[3] > box[1] \
        and box[2] > 0 and box[3] > 0 and box[0] < size["width"] and box[1] < size["height"]


def shown(root: ElementTree.Element, size: dict, package: str) -> set[str]:
    mine = owned(root, package)
    return {f for n in root.iter("node") if id(n) in mine and (f := feature(n)) and on_screen(n, size)}


def present(root: ElementTree.Element, package: str) -> set[str]:
    mine = owned(root, package)
    return {f for n in root.iter("node") if id(n) in mine and (f := feature(n))}


def labelled(node: ElementTree.Element) -> bool:
    """Some text or description a reader speaks: an exported `feature:<id>` is an identity, not a label."""

    return any(n.get("text", "").strip() or (d := n.get("content-desc", "").strip()) and not d.startswith("feature:")
               for n in node.iter("node"))


def owned(root: ElementTree.Element, package: str) -> set[int]:
    """The app's own nodes: its package's, and every view under a `feature:<id>` System UI draws, which is the
    app's notification in the shade. Another app's, such as the fixture viewer's, stay out even when tagged."""

    out = {id(n) for n in root.iter("node") if n.get("package") == package}
    for n in root.iter("node"):
        if n.get("package") == SYSTEM_UI and feature(n):
            out |= {id(child) for child in n.iter("node")}
    return out


def mechanical(root: ElementTree.Element, package: str, size: dict, pixels) -> list[str]:
    """What the accessibility scanner and a person would catch on the app's own nodes: a view past the
    screen's edge, a touch target under 48 dp, a control with no label, and text without contrast."""

    out, least, mine = [], TARGET_DP * size["dpi"] / 160, owned(root, package)
    for node in root.iter("node"):
        box = bounds(node)
        if id(node) not in mine or not box or box[2] <= box[0] or box[3] <= box[1] \
                or node.get("visible-to-user", "true") != "true":
            continue
        name = node.get("resource-id") or node.get("text") or node.get("class", "")
        if box[0] < 0 or box[1] < 0 or box[2] > size["width"] or box[3] > size["height"]:
            out.append(f"`{name[:60]}` 가 화면 밖으로 나간다 {node.get('bounds')}")
        if node.get("clickable") == "true":
            if min(box[2] - box[0], box[3] - box[1]) < least:
                out.append(f"`{name[:60]}` 의 터치 영역이 {TARGET_DP}dp 보다 작다 {node.get('bounds')}")
            if not labelled(node):
                out.append(f"`{name[:60]}` 에 읽을 이름(text·contentDescription)이 없다")
        if node.get("text", "").strip() and pixels and (ratio := contrast(pixels, box)) < MIN_CONTRAST:
            out.append(f"`{node.get('text')[:40]}` 의 명암비가 {ratio:.1f}:1 이다 ({MIN_CONTRAST}:1 미만)")
    return list(dict.fromkeys(out))


def raw(data: bytes) -> tuple[int, int, bytes] | None:
    """`screencap`'s raw RGBA frame: width, height and the pixels after its 12- or 16-byte header."""

    if len(data) < 16:
        return None
    width, height = int.from_bytes(data[:4], "little"), int.from_bytes(data[4:8], "little")
    header = len(data) - width * height * 4
    return (width, height, data[header:]) if header in (12, 16) and width and height else None


def luminance(r: int, g: int, b: int) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(pixels: tuple[int, int, bytes], box: tuple[int, int, int, int]) -> float:
    """The contrast between the text's ink and its ground inside `box`, WCAG's ratio of the darkest and the
    lightest twentieth of its pixels. ponytail: percentiles of one box, not glyph segmentation; it misreads
    text on a photograph or a gradient, where the critic still judges what it sees."""

    width, height, data = pixels
    x1, y1, x2, y2 = max(box[0], 0), max(box[1], 0), min(box[2], width), min(box[3], height)
    step = max(1, min(x2 - x1, y2 - y1) // 24)
    found = sorted(luminance(*data[(y * width + x) * 4:(y * width + x) * 4 + 3])
                   for y in range(y1, y2, step) for x in range(x1, x2, step))
    if len(found) < 20:
        return 21.0
    dark, light = found[len(found) // 20], found[-1 - len(found) // 20]
    return (light + 0.05) / (dark + 0.05)


# -- Scenarios -----------------------------------------------------------------------------------------

def instrumented(output: str) -> list[tuple[str, str, int, str]]:
    """`am instrument -r`'s results: `(class, test, code, stack)` for each test that finished."""

    out, status = [], {}
    for line in output.splitlines():
        if line.startswith("INSTRUMENTATION_STATUS: "):
            key, _, value = line[len("INSTRUMENTATION_STATUS: "):].partition("=")
            status[key] = value
        elif line.startswith("INSTRUMENTATION_STATUS_CODE: "):
            code = int(line.split(":", 1)[1].strip() or 0)
            if code != 1:   # 1 starts a test; 0 passed, -2 failed, -1 errored, -3 and -4 skipped
                out.append((status.get("class", ""), status.get("test", ""), code, status.get("stack", "")))
            status = {}
    return out


def completed(output: str) -> bool:
    """The run reached its end: `am instrument` reports `INSTRUMENTATION_CODE: -1` (`RESULT_OK`) only after
    the runner finished, and no record aborted, failed or crashed it on the way. The records are read at the
    start of a line, so a test named for the word, inside a status line, is not one."""

    lines = output.splitlines()
    ended = [line.split(":", 1)[1].strip() for line in lines if line.startswith("INSTRUMENTATION_CODE:")]
    return ended == ["-1"] and not any(line.startswith(("INSTRUMENTATION_ABORTED:", "INSTRUMENTATION_FAILED:",
                                                        "INSTRUMENTATION_RESULT: shortMsg=")) for line in lines)


def covers(test: str, rid: str, kind: str = "scenario") -> bool:
    """`test` is `rid`'s scenario test (or owed check's, `kind` `owed`): `scenario_<id>` itself or followed by
    `_`, so R10's never stands in for R1's."""

    name, stem = test.lower(), f"{kind}_{rid.lower()}"
    return name == stem or name.startswith(stem + "_")


def scenario_findings(output: str, wanted: list[str], first: str, owing: list[dict] = ()) -> list[dict]:
    results, out = instrumented(output), []
    if not completed(output):
        out.append(finding(first, "full", f"시나리오 테스트가 끝까지 돌지 못했다 — {output.strip()[-300:]}"))
    for cls, test, code, stack in results:
        if code != 0:
            what = "건너뛰었다" if code in (-3, -4) else "실패했다"
            out.append(finding(first, "full", f"시나리오 테스트 `{cls}#{test}` 가 {what} — {stack.strip()[:200]}"))
    for rid in wanted:
        if not any(covers(test, rid) for _, test, _, _ in results):
            out.append(finding(first, "full", f"시나리오 {rid} 의 계측 테스트(`scenario_{rid}_…`)가 돌지 않았다"))
    for x in owing:
        if not any(covers(test, x["id"], "owed") for _, test, _, _ in results):
            out.append(finding(first, "full", f"기기에서 확인할 {x['id']}({x['requirement']}) 의 계측 테스트"
                                              f"(`owed_{x['id']}_…`)가 돌지 않았다 — {x['reason']}"))
    return out


# -- The look ------------------------------------------------------------------------------------------

def look(tree: Path, device: dict, spec: dict, base: Path, folder: Path, stopped=never) -> list[dict]:
    """Every screen in every state shot into `folder` as `<screen>-phone-<state>.png` on an emulator the run
    owns under `base`; the findings, placement and scenarios included. The emulator is cleaned on every exit."""

    size = DEVICES[spec["oneshot"]["profile"]["device"]]
    cleaned(base)
    home = base / f"r{time.time_ns()}"
    home.mkdir(parents=True)
    with android_owned.boot(home, tools(), device={k: size[k] for k in ("width", "height", "dpi")}):
        halted(stopped)
        d = Device(home, device, size, tree)
        for apk, package in apks(device):
            d.install(apk, package)
        return inspect(d, spec, folder, stopped)


def digests(tree: Path, device: dict) -> dict[str, str]:
    """Each APK's SHA-256: the phone round installs exactly what the emulator's look passed."""

    return {apk: android_owned.sha256(tree / apk) for apk, _ in apks(device)}


def frozen(tree: Path, device: dict, into: Path) -> dict[str, str]:
    """Each APK copied into `into`, a folder the run owns, and the copies' digests. The APKs are build outputs
    Git does not track, so a build outside the run can change them; a device installs the copies instead."""

    for apk, _ in apks(device):
        (into / apk).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tree / apk, into / apk)
    return digests(into, device)


def apks(device: dict) -> list[tuple[str, str]]:
    """Each APK the block names with the package it installs: the app, its tests, and the fixture viewer."""

    return [(device["apk"], device["package"]), (device["tests"]["apk"], device["tests"]["runner"].split("/")[0]),
            *([(device["fixture"]["apk"], device["fixture"]["package"])] if "fixture" in device else [])]


def inspect(d: Device, spec: dict, folder: Path, stopped=never) -> list[dict]:
    """On a device with the APKs installed: every screen in every state shot into `folder`, the mechanical,
    placement and coverage findings, and the scenarios. The emulator's look and the phone round share it."""

    o = spec["oneshot"]
    front, frequency = o["front"], o["frequency"]
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    # `seen`: in a `full` hierarchy, where every shown feature must appear; `anywhere`: in any state's, where
    # a `never` feature must not.
    findings, seen, anywhere = [], {f: False for f in frequency}, set()
    d.grant(grants(spec), "fixture" in d.d)
    for s in front["screens"]:
        for state in STATES:
            halted(stopped)
            findings += shot(d, s, state, folder, seen, anywhere)
    halted(stopped)
    findings += placement(d, front, frequency, stopped)
    halted(stopped)
    first = next(s for s in front["screens"] if s["primary"])
    try:
        output = d.adb("shell", "am", "instrument", "-w", "-r", d.d["tests"]["runner"], timeout=TESTS_SECONDS)
    except ValueError as exc:   # a runner the test APK does not hold exits non-zero
        output = f"INSTRUMENTATION_FAILED: {exc}"
    findings += scenario_findings(output, scenarios(spec), first["id"], owed(spec))
    if why := d.died():
        findings.append(finding(first["id"], "full", f"시나리오 테스트 중에 {why}"))
    for f, cls in frequency.items():
        if cls == "never" and f in anywhere:
            findings.append(finding(first["id"], "full", f"화면에 없기로 한 `{f}` 의 컨트롤이 화면에 있다"))
        if cls != "never" and not seen[f]:
            findings.append(finding(first["id"], "full", f"`feature:{f}` 가 어느 화면의 계층에도 없다"))
    return findings


def shot(d: Device, s: dict, state: str, folder: Path, seen: dict, anywhere: set) -> list[dict]:
    if why := d.open(s, state):
        return [finding(s["id"], state, why)]
    try:
        root = d.hierarchy()
        (folder / f"{s['id']}-{VIEW}-{state}.png").write_bytes(d.adb("exec-out", "screencap", "-p", binary=True))
        pixels = raw(d.adb("exec-out", "screencap", binary=True))
    finally:
        d.close(s)
    out = [finding(s["id"], state, what) for what in mechanical(root, d.d["package"], d.size, pixels)]
    if why := d.died():
        out.append(finding(s["id"], state, why))
    there = present(root, d.d["package"])
    anywhere |= there
    if state == "full":
        for f in there & set(seen):
            seen[f] = True
    return out


def placement(d: Device, front: dict, frequency: dict, stopped=never) -> list[dict]:
    """The owner's map read off the first screen's hierarchy in the `full` state; each breach is high."""

    first = next(s for s in front["screens"] if s["primary"])
    if why := d.open(first, "full"):
        return [finding(first["id"], "full", why)]
    root = d.hierarchy()
    d.close(first)
    visible, there, out = shown(root, d.size, d.d["package"]), present(root, d.d["package"]), []
    for f, cls in frequency.items():
        if cls == "every" and f not in visible:
            out.append(finding(first["id"], "full", f"늘 쓰는 기능 `{f}` 가 첫 화면에 바로 보이지 않는다"))
        if cls in ("rarely", "never") and f in there:
            out.append(finding(first["id"], "full", f"`{f}` 는 첫 화면에 없어야 한다"))
        if cls == "sometimes" and f not in visible and not one_tap(d, first, root, f, stopped):
            out.append(finding(first["id"], "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out


def one_tap(d: Device, first: dict, root: ElementTree.Element, fid: str, stopped=never) -> bool:
    """Whether a single tap on one of the first screen's controls in view makes the feature visible."""

    targets = [bounds(n) for n in root.iter("node")
               if n.get("clickable") == "true" and n.get("package") == d.d["package"] and on_screen(n, d.size)]
    for box in targets[:MAX_TAPS]:
        halted(stopped)
        if d.open(first, "full"):
            return False
        d.tap((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        after = d.hierarchy()
        d.close(first)
        if fid in shown(after, d.size, d.d["package"]):
            return True
    return False
