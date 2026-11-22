"""oneshot's phone round for an Android app: after the emulator loop's two clean critiques, one round on the
owner's own phone before R3 (step 7f1).

`docs/plans/oneshot/7-android.md` ("Before R3 — one round on the owner's phone") is the design. The run waits
at `phone`, saved, with no timeout: a restart returns to the same prompt. The owner picks a connected phone
by its serial; the round installs exactly the APKs the emulator's look passed, under the run's own package
ids, after an ownership record names them. On the phone it runs the same screens, placement and scenarios as
the emulator, then reads the app's own frame timing and memory. It uninstalls what the record names on every
outcome, unless the owner keeps a passing app; a cleanup the phone's absence cut short stays pending and is
finished when that serial is listed again. A high finding goes back to the build as the round's findings,
then through the emulator loop to the phone again. A pass opens R3, whose sheet shows the round (step 7f2).
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import android_owned
import android_phone

from . import oneshot, oneshot_android, oneshot_build, oneshot_device as device, oneshot_front_build as front_build, \
    oneshot_review as review, oneshot_spec, runtime
from .oneshot_profile import kinds_of, shape

STAGE = "phone"
SERIAL = re.compile(r"[A-Za-z0-9._:-]{1,64}")
# Where the package manager keeps an app: internal storage, or an adopted volume (`/mnt/expand/<uuid>/app`).
SHOT = re.compile(r"S[1-9]\d*-phone-(empty|full|loading|error)\.png")
APK_PATH = re.compile(r"(/data|/mnt/expand/[0-9a-fA-F-]{1,64})/app/[A-Za-z0-9._=~+/-]+/base\.apk")
LOCKED = ("mDreamingLockscreen=true", "isKeyguardShowing=true", "mShowingLockscreen=true")
SWIPES = 4

router = APIRouter()
_adb = threading.Lock()   # one owned USB server at a time: Windows lets one ADB server hold a phone
_halts: dict[str, threading.Event] = {}
# A stop and the round's own end are decided under one lock: once a round settled what stays on the phone
# (`_final`), a stop is refused, so an accepted stop always means the apps come off and nothing passes.
_ending = threading.Lock()
_final: set[str] = set()


def base(repo: str) -> Path:
    return oneshot_spec.store(repo).folder / "phone"


def adb_tool() -> dict:
    """The card's `adb` with its digest now: the phone round needs no emulator or system image."""

    adb = Path(oneshot_android.status()["sdk"]) / "platform-tools" / f"adb{oneshot_android.EXE}"
    if not adb.is_file():
        raise ValueError("adb 가 없다 — 환경 카드에서 Android 도구를 설치한다")
    return {"path": str(adb), "sha256": android_owned.sha256(adb)}


@contextmanager
def phone_server(repo: str):
    """The run's own USB ADB server in a fresh folder, an earlier one cleaned first; one at a time."""

    if not _adb.acquire(blocking=False):
        raise ValueError("다른 휴대폰 작업이 ADB 서버를 쓰고 있다 — 끝난 뒤 다시 한다")
    try:
        device.cleaned(base(repo))
        home = base(repo) / f"r{time.time_ns()}"
        home.mkdir(parents=True)
        with android_phone.serve(home, adb_tool()):
            yield home
    finally:
        _adb.release()


# -- What the run owns on a phone ------------------------------------------------------------------------

def call(home: Path, *args, timeout: float = android_owned.CALL_SECONDS) -> str:
    return android_owned.call(home, list(args), timeout=timeout)


def on_phone(home: Path, package: str) -> bool:
    """Whether `package` is installed: `pm list packages` matches a prefix, so the line is read exactly."""

    return f"package:{package}" in call(home, "shell", "pm", "list", "packages", package).split()


def base_apk(home: Path, package: str) -> str | None:
    """`pm path`'s `base.apk` for `package`, or `None` unless it is a plain path under where apps are kept:
    it goes into the phone's shell."""

    paths = [line.removeprefix("package:").strip() for line in call(home, "shell", "pm", "path", package).splitlines()
             if line.startswith("package:")]
    apk = next((p for p in paths if p.endswith("/base.apk")), "")
    return apk if APK_PATH.fullmatch(apk) and "/../" not in apk and "/./" not in apk else None


def installed_digest(home: Path, package: str) -> str | None:
    """The SHA-256 of the APK the phone holds for `package`; `None` when unreadable. An install copies the APK
    as it is, so the digest says which APK it is."""

    if not (apk := base_apk(home, package)):
        return None
    found = call(home, "shell", "sha256sum", apk).split()
    return found[0].lower() if found and re.fullmatch(r"[0-9a-fA-F]{64}", found[0]) else None


def instance(home: Path, package: str) -> str | None:
    """Which installation of `package` this is, or `None` when unreadable: its `base.apk` path, which the
    package manager draws afresh on each install since Android 11, with that file's inode and modification
    time in epoch seconds, which a reinstall writes anew and no time zone changes."""

    if not (apk := base_apk(home, package)):
        return None
    # `adb shell` joins its words with spaces, unquoted: the format holds none.
    found = call(home, "shell", "stat", "-c", "%i:%Y", apk).strip()
    return f"{apk} {found}" if re.fullmatch(r"\d+:\d+", found) else None


def uninstalled(home: Path, digests: dict[str, str], installed: dict[str, str | None]) \
        -> tuple[list[str], list[str], list[str]]:
    """Each package removed that is still the very installation this run made (`installed`, read right after
    its install) and holds exactly that APK, then checked gone. What is left (still there, or unknown on a
    phone that left), what is another installation (another APK: the run only ever installs its own), and
    what may be the run's APK with no proof it is the run's installation: an install a crash or a failed
    command cut short, or the owner's reinstall of the same APK. Neither of the last two is ever touched."""

    left, foreign, unsure = [], [], []
    for package, digest in digests.items():
        try:
            if not on_phone(home, package):
                continue
            found = installed_digest(home, package)
            if found is not None and found != digest:
                foreign.append(package)
            elif not installed.get(package) or (found is not None and instance(home, package) != installed[package]):
                unsure.append(package)
            elif found is None:
                left.append(package)
            else:
                call(home, "uninstall", package)
                if on_phone(home, package):
                    left.append(package)
        except ValueError:
            left.append(package)
    return left, foreign, unsure


def settle(repo: str, home: Path, index: int) -> None:
    """Record `index`'s cleanup on the bound phone. `unsure` stays shown until the owner removes those apps:
    the run cannot tell its installation from the owner's of the same APK."""

    r = oneshot_spec.spec_of(repo)["oneshot"]["phone_owned"][index]
    left, foreign, unsure = uninstalled(home, r["digests"], r.get("installed") or {})
    state = "pending" if left else "unsure" if unsure else "cleaned"
    recorded(repo, index, state=state, left=left, foreign=foreign, unsure=unsure, ts=time.time())


def owning(o: dict) -> list[dict]:
    """Records whose packages may still be on a phone: a cleanup that did not finish, a round cut off, or an
    app the run cannot prove it installed."""

    return [r for r in o.get("phone_owned") or [] if r["state"] in ("owning", "pending", "unsure")]


def recorded(repo: str, index: int | None = None, **fields) -> int:
    """`fields` merged into ownership record `index`, or appended as a new one; its index."""

    with oneshot._lock:
        records = list(oneshot_spec.spec_of(repo)["oneshot"].get("phone_owned") or [])
        if index is None:
            records.append(fields)
            index = len(records) - 1
        else:
            records[index] = {**records[index], **fields}
        oneshot_spec.saved(repo, oneshot={"phone_owned": records})
    return index


def tidy(repo: str, home: Path, found: list[dict]) -> None:
    """Every unfinished cleanup whose phone is listed and ready, finished; the server stays bound to nothing
    else afterwards."""

    listed = {x["serial"] for x in found if x["state"] == "device"}
    for index, r in enumerate(oneshot_spec.spec_of(repo)["oneshot"].get("phone_owned") or []):
        if r["state"] in ("owning", "pending", "unsure") and r["serial"] in listed:
            android_phone.bind(home, r["serial"])
            settle(repo, home, index)
    android_phone.bind(home, "")


# -- The round -----------------------------------------------------------------------------------------

def measured(home: Path) -> dict:
    """The phone's screen as it draws now: an override size or density wins over the physical one."""

    sizes = re.findall(r"(\d+)x(\d+)", call(home, "shell", "wm", "size"))
    dpis = re.findall(r"(\d+)", call(home, "shell", "wm", "density"))
    if not sizes or not dpis:
        raise ValueError("휴대폰의 화면 크기를 읽지 못했다")
    return {"width": int(sizes[-1][0]), "height": int(sizes[-1][1]), "dpi": int(dpis[-1])}


def locked(home: Path) -> bool:
    call(home, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    return any(word in call(home, "shell", "dumpsys", "window") for word in LOCKED)


def timing(text: str) -> dict:
    """`dumpsys gfxinfo <package>`'s frames: rendered, janky and the percentiles in milliseconds."""

    def number(pattern: str) -> float | None:
        found = re.search(pattern, text)
        return float(found.group(1)) if found else None

    return {"frames": number(r"Total frames rendered:\s*(\d+)"), "janky": number(r"Janky frames:\s*(\d+)"),
            "janky_percent": number(r"Janky frames:\s*\d+\s*\(([\d.]+)%\)"),
            **{f"p{p}_ms": number(rf"{p}th percentile:\s*([\d.]+)ms") for p in (50, 90, 99)}}


def memory(text: str) -> int | None:
    """`dumpsys meminfo <package>`'s total PSS in kilobytes."""

    found = re.search(r"TOTAL PSS:\s*(\d+)", text) or re.search(r"^\s*TOTAL\s+(\d+)", text, re.M)
    return int(found.group(1)) if found else None


def measure(d: device.Device, spec: dict) -> tuple[dict, int | None]:
    """The first screen scrolled a few times on its own frame counters, then the app's memory."""

    first = next(s for s in spec["oneshot"]["front"]["screens"] if s["primary"])
    package, w, h = d.d["package"], d.size["width"], d.size["height"]
    if d.open(first, "full"):
        return {}, None
    d.adb("shell", "dumpsys", "gfxinfo", package, "reset")
    for n in range(SWIPES):
        y1, y2 = (h * 3 // 4, h // 4) if n % 2 == 0 else (h // 4, h * 3 // 4)
        d.adb("shell", "input", "swipe", str(w // 2), str(y1), str(w // 2), str(y2), "300")
    frames = timing(d.adb("shell", "dumpsys", "gfxinfo", package))
    used = memory(d.adb("shell", "dumpsys", "meminfo", package))
    d.close(first)
    return frames, used


def drive(repo: str, home: Path, spec: dict, frozen: Path, stopped) -> dict:
    """The round on the picked phone, installing from `frozen`, the APKs copied out of the worktree and checked
    against the emulator's look: its outcome, `passed`, `failed` with findings, or why it did not run."""

    o = spec["oneshot"]
    serial, keep, n = o["phone"]["serial"], bool(o["phone"].get("keep")), int(o["phone"].get("round") or 0) + 1
    found = android_phone.listed(home)
    tidy(repo, home, found)
    picked = next((x for x in found if x["serial"] == serial), None)
    if not picked or picked["state"] != "device":
        return {"status": "missing", "detail": f"`{serial}` 휴대폰이 연결돼 있지 않거나 USB 디버깅을 허용하지 않았다"}
    android_phone.bind(home, serial)
    if locked(home):
        return {"status": "locked", "detail": "휴대폰이 잠겨 있다 — 잠금을 풀고 화면을 켠 채로 다시 시작한다"}
    packages = [p for _, p in device.apks(o["device"])]
    if taken := [p for p in packages if on_phone(home, p)]:
        return {"status": "taken", "detail": f"휴대폰에 `{', '.join(taken)}` 가 이미 있다 — 덮어 설치하지 않는다. "
                                              "지운 뒤 다시 시작하거나 다른 휴대폰을 고른다"}
    size = measured(home)
    # Saved before the first install: whatever happens next, the record names what may be on the phone and
    # the digest each package must hold. Only the very installation in `installed` is ever removed: a package
    # joins it once its install command succeeded (under `-R`, the run's own), with that installation's
    # `instance` read right after. The same APK the owner installed, before or since, is not the run's.
    digests = {package: o["apks"][apk] for apk, package in device.apks(o["device"])}
    index = recorded(repo, serial=serial, model=picked["model"], packages=packages, digests=digests,
                     installed={}, state="owning", left=[], ts=time.time())
    passed, installed = False, {}

    def mine(package: str, which: str | None) -> None:
        installed[package] = which
        recorded(repo, index, installed=dict(installed))

    try:
        d = device.Device(home, o["device"], size, frozen)
        for apk, package in device.apks(o["device"]):
            # Never over an installation: an id taken since the check fails the install here.
            d.install(apk, package, lambda p=package: mine(p, None))
            mine(package, instance(home, package))
            if installed_digest(home, package) != digests[package]:
                raise ValueError(f"휴대폰에 설치된 `{package}` 가 에뮬레이터 검사를 통과한 APK 와 다르다")
        findings = device.inspect(d, spec, base(repo) / "shots" / f"p{n}", stopped)
        device.halted(stopped)   # a stop during the scenarios: never a pass, and the apps come off
        if locked(home):   # the screen went off mid-round: its shots show the lock, not the app
            return {"status": "locked", "detail": "검사 중에 휴대폰이 잠겼다 — 화면 꺼짐 시간을 늘리고 다시 시작한다"}
        frames, used = measure(d, spec)
        device.halted(stopped)
        passed = not any(f["severity"] == "high" for f in findings)
        return {"status": "passed" if passed else "failed", "round": n, "model": picked["model"], "size": size,
                "findings": findings, "timing": frames, "memory_kb": used, "head": o["front_built_head"],
                "apks": o["apks"], "front_build": o.get("front_build"), "detail": ""}
    finally:
        try:
            call(home, "shell", "rm", "-f", device.DUMP)
        except ValueError:
            pass
        with _ending:   # from here a stop is refused; one accepted before it takes the cleanup
            if final := not stopped():
                _final.add(repo)
        if passed and keep and final:
            recorded(repo, index, state="released", ts=time.time())
        else:
            settle(repo, home, index)


def phone_round(repo: str) -> None:
    """The worker: the round holds the worktree on the HEAD the emulator's look passed, as the critic does.
    Its halt was cleared by the pick that started it: a stop accepted since then is never lost."""

    halt = _halts.setdefault(repo, threading.Event())
    stopped = lambda: halt.is_set() or runtime.stopping.is_set()   # noqa: E731

    def body(store, spec, tree):
        from . import oneshot_critique   # it imports this module
        o = spec["oneshot"]
        if why := oneshot_critique.stale(o):   # a PC app's look the critique would not count, as an earlier version saved
            review.rebuild(repo, store, why, front_build.STAGE)
            return None
        if shape(o) == "kinds":   # the phone sees only the Android app's screens, features and scenarios
            spec = front_build.as_kind(spec, "android")
        # The round installs copies, checked here against the digests of the copies the emulator installed.
        frozen = base(repo) / "apks" / f"r{time.time_ns()}"
        try:
            if why := device.built(tree, o["device"]):
                raise ValueError(why)
            same = device.frozen(tree, o["device"], frozen) == o.get("apks")
        except (OSError, ValueError) as exc:
            shutil.rmtree(frozen, ignore_errors=True)
            review.rebuild(repo, store, f"휴대폰에 올릴 APK 를 읽지 못했다 — {exc}", front_build.STAGE)
            return None
        if not same:
            shutil.rmtree(frozen, ignore_errors=True)
            review.rebuild(repo, store, "휴대폰에 올릴 APK 가 에뮬레이터 검사를 통과한 것과 다르다", front_build.STAGE)
            return None
        try:
            with phone_server(repo) as home:
                outcome = drive(repo, home, spec, frozen, stopped)
        except Exception as exc:   # a phone pulled out, a refused command, a stop: the prompt stands again
            outcome = {"status": "stopped" if stopped() else "error",
                       "detail": "휴대폰 회차를 멈췄다" if stopped() else f"휴대폰 회차가 끝나지 못했다 — {exc}"}
        finally:
            shutil.rmtree(frozen, ignore_errors=True)
        if repo not in _final and stopped():   # a stop accepted before the round ended: never its outcome
            outcome = {"status": "stopped", "detail": "휴대폰 회차를 멈췄다"}
        return lambda: settled(repo, outcome)

    review.holding(repo, "휴대폰 회차", body, "front_built_head", front_build.STAGE)


def settled(repo: str, outcome: dict):
    """The outcome applied: on a pass, the next device round or R3's sheet (its brief written once the tree is let
    go), the build with the phone's findings on a fail, else the prompt."""

    from . import oneshot_critique, oneshot_pc   # they import this module
    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        result = {**(o.get("phone") or {}), **outcome}
        if outcome["status"] == "passed":
            return oneshot_pc.onward(repo, {"phone": result})
        if outcome["status"] != "failed":
            oneshot_spec.saved(repo, oneshot={"phone": {**result, "status": "waiting"}, "working": None})
            return None
        high = [f for f in outcome["findings"] if f["severity"] == "high"]
        # As the critic's fixes: `working` and `fix` are saved before the turn goes, and the stop rule counts
        # its two clean rounds afresh, so the phone sees the fix only after the emulator loop passed it again.
        spec = oneshot_spec.saved(repo, oneshot={
            "phone": result, "stage": front_build.STAGE, "attempts": 0, "working": front_build.STAGE,
            "critique_base": len(o.get("critiques") or []),
            "fix": {"who": f"The run's round on the owner's phone ({outcome['model'] or 'a physical phone'}) found these",
                    "findings": high}})
    return lambda: oneshot_build.send(repo, front_build.STAGE, oneshot_critique.front_turn(spec))


def waits(repo: str, fields: dict) -> dict:
    """The prompt to connect the phone, with `fields` saved: the critiques the round counted on."""

    o = oneshot_spec.spec_of(repo)["oneshot"]
    phone = {**(o.get("phone") or {}), "status": "waiting", "detail": ""}
    return oneshot_spec.saved(repo, oneshot={**fields, "stage": STAGE, "working": None, "phone": phone})


def earlier(repo: str, o: dict) -> None:
    """A run an earlier version stopped before R3 for its kind: with no phone round passed on its current HEAD, APKs
    and build, this version waits for the phone; with one, it opens R3's sheet."""

    from . import oneshot_critique, oneshot_pc   # they import this module
    stop = o.get("stopped") or {}
    if "android" not in kinds_of(o) or o.get("stage") != "r3" or stop.get("reason") != "kind" or oneshot_spec.held(o):
        return   # a run this version still holds before R3 (with `desktop`) stays as it is
    # The guard's own test, on the worktree as it is: a pass of another HEAD, APKs or build, or a worktree moved off
    # its HEAD, waits for the phone, whose round sends a moved tree back to the build.
    if oneshot_pc.phoned(o) and oneshot_pc.at_head(repo, o.get("front_built_head")):
        oneshot_spec.saved(repo, oneshot={"stopped": None, "working": None, "r3": oneshot_critique.opening(o)})
    else:
        oneshot_spec.saved(repo, oneshot={"stage": STAGE, "stopped": None, "working": None,
                                          "phone": {**(o.get("phone") or {}), "status": "waiting", "detail": ""}})


oneshot_spec.WORKERS[STAGE] = phone_round
oneshot_spec.SHEETS.add(STAGE)   # a round the server went down in: `[재개]` puts the prompt back
oneshot_spec.WAITS.add(STAGE)
oneshot_spec.RECOVERS.append(earlier)


# -- HTTP ----------------------------------------------------------------------------------------------

class Pick(BaseModel):
    serial: str
    keep: bool = False


def refused(exc: Exception) -> HTTPException:
    return HTTPException(409, str(exc))


@router.get("/api/oneshot/spec/{repo}/phone/shot/{n}/{name}")
def phone_shot(repo: str, n: int, name: str) -> FileResponse:
    """A phone round's screenshot, for R3's sheet."""

    oneshot_spec.spec_of(repo)   # a repository with a oneshot spec, never a path
    path = SHOT.fullmatch(name) and oneshot_spec.within(oneshot_spec.store(repo).folder, "phone", "shots", f"p{n}", name)
    if not path or not path.is_file():   # a link or a junction on the way never serves a file outside the run
        raise HTTPException(404, "그런 휴대폰 사진이 없다")
    return FileResponse(path, media_type="image/png")


@router.get("/api/oneshot/spec/{repo}/phone/devices")
def devices(repo: str) -> dict:
    """The phones the run's own server sees, each with its state and model; a pending cleanup on one of
    them is finished on the way. At any stage: a passed round's cleanup can be pending at R3."""

    if oneshot_spec.spec_of(repo)["oneshot"].get("working") == STAGE:
        raise HTTPException(409, "휴대폰 회차가 돌고 있다 — 끝난 뒤 다시 한다")
    try:
        with phone_server(repo) as home:
            found = android_phone.listed(home)
            tidy(repo, home, found)
    except ValueError as exc:
        raise refused(exc) from exc
    return {"devices": found, "owned": owning(oneshot_spec.spec_of(repo)["oneshot"])}


@router.post("/api/oneshot/spec/{repo}/phone")
def pick(repo: str, body: Pick) -> dict:
    if not SERIAL.fullmatch(body.serial):
        raise HTTPException(400, "휴대폰의 시리얼이 아니다")
    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        oneshot_spec.idle(o, STAGE)
        phone = {**(o.get("phone") or {}), "status": "running", "serial": body.serial, "keep": body.keep, "detail": ""}
        with _ending:   # a fresh round: cleared before `working` names it, so a stop from then on holds
            _halts.setdefault(repo, threading.Event()).clear()
            _final.discard(repo)
        oneshot_spec.start(repo, STAGE, phone=phone)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/phone/stop")
def stop(repo: str) -> dict:
    """The running round stops at its next step; what it installed is removed on the way out. A round that
    already settled what stays on the phone is past stopping."""

    if oneshot_spec.spec_of(repo)["oneshot"].get("working") != STAGE:
        raise HTTPException(409, "휴대폰 회차가 돌고 있지 않다")
    with _ending:   # taken after the spec's lock, never inside it: `pick` holds the spec's lock, then this
        if repo in _final:
            raise HTTPException(409, "휴대폰 회차가 이미 끝나는 중이다")
        _halts.setdefault(repo, threading.Event()).set()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def extra(spec: dict) -> dict:
    """An unfinished cleanup is shown on the run until it is done, whatever stage the run is at."""

    o = spec["oneshot"]
    return {"phone_owned": left} if (left := owning(o)) and o.get("working") != STAGE else {}


oneshot_spec.VIEWS.append(extra)
