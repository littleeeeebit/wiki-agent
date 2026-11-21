"""oneshot S7's build: one work turn builds the approved screens, the server checks it and shoots it.

`docs/plans/oneshot/4-frontend.md` ("S7 — Build") is the design. The turn runs as the backend's did
(`oneshot_build.send`), and only while it runs may it write the worktree's `.wiki/adapter.toml`: it fills
`live_cmd` and widens the gate. The check reads the committed adapter back, runs the gate, keeps the
contract tests pinned and passing, then starts the app and shoots it (`oneshot_look`). A high finding
goes back to the turn. A pass leaves the run at `critique`, where the critic takes it on.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from wiki import slots_for

from . import oneshot, oneshot_build, oneshot_desktop, oneshot_desktop_look, oneshot_device, oneshot_look, \
    oneshot_needs, oneshot_screens as screens, oneshot_spec, runtime, specs
from .oneshot_profile import DEVICES, environment, kinds_of, schema, shape
from .oneshot_sheet import KIND_NAMES

STAGE = "build-front"
# The several-kinds prompt keeps a kind's parts only when the owner picked it, and takes the PC app's conventions,
# block and freeze from the PC app's own prompt, so the two never drift apart.
SECTION = re.compile(r"<!-- kind:(\w+) -->\n(.*?)<!-- /kind -->\n", re.S)
SHARED = re.compile(r"<!-- shared:(\w+) -->\n?(.*?)\n?<!-- /shared -->", re.S)
WHO = {"webapp": "a web app", "android": "an Android app", "desktop": "a PC app (a Windows program)"}
BLOCKS = {"webapp": ("`live_cmd` is filled", None),
          "android": ("the `oneshot-device` block names each APK", "the `oneshot-device` block"),
          "desktop": ("the `oneshot-desktop` block has the shape above", "the `oneshot-desktop` block")}


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def for_kinds(text: str, kinds: list[str]) -> str:
    """The several-kinds prompt for the owner's kinds: each kind's parts, the PC app's from its own prompt."""

    text = SECTION.sub(lambda m: m.group(2) if m.group(1) in kinds else "", text)
    pc = {name: part for name, part in SHARED.findall(oneshot.prompt(STAGE, "desktop"))}
    ends = [end for k in kinds if (end := BLOCKS[k][1])]
    return text.replace("{kinds}", listed([WHO[k] for k in kinds])) \
        .replace("{checked_blocks}", listed([BLOCKS[k][0] for k in kinds])) \
        .replace("{end_blocks}", f" and {listed(ends)}" if ends else "") \
        .replace("{desktop_conventions}", pc["conventions"]).replace("{desktop_block}", pc["block"]) \
        .replace("{desktop_freeze}", pc["freeze"]) \
        .replace("{desktop_look}", (pc["look"][0].lower() + pc["look"][1:]).replace("\n   ", "\n     "))   # a bullet


views = screens.views


def as_kind(spec: dict, kind: str) -> dict:
    """The run as a run of `kind` alone would hold it: that kind's screens, the features and requirements that
    apply to it, its card's fields and its first screen. Each kind's S7 checks read only this (plan 9c)."""

    o, front = spec["oneshot"], spec["oneshot"]["front"]
    mine = [{k: v for k, v in s.items() if k != "kind"} for s in front["screens"] if s.get("kind") == kind]
    ids, feats = {s["id"] for s in mine}, [f for f in o["features"] if kind in f["kinds"]]
    one = {"screens": mine, "states": {i: st for i, st in front["states"].items() if i in ids},
           "mocks": {i: m for i, m in front["mocks"].items() if i in ids}, "tokens": front["tokens"],
           "shots": front.get("shots"),
           **({"preview": p} if (p := screens.previews(front, "").get(kind)) else {})}
    ops = [f"{p['method'].upper()} {p['path']}" for p in o["design"].get("api", [])]   # the web app's served API
    return {**spec, "requirements": [r for r in spec.get("requirements") or [] if kind in r.get("kinds", [kind])],
            "oneshot": {**o, "intent": {**o["intent"], "kind": kind, "kinds": [kind]}, "front": one, "features": feats,
                        "frequency": {f["id"]: o["frequency"][f["id"]] for f in feats},
                        "profile": {k: v for k, v in o["profile"].items() if k in schema(kind)},
                        "first_screen": o["first_screen"][kind], **({"inventory": ops} if kind == "webapp" else {})}}


def designed(o: dict, palette: dict) -> dict:
    """What the build turn builds: the shared tokens, then each kind's screens, states, features and map."""

    front = o["front"]
    shared = {"type": front["tokens"]["type"], "spacing": front["tokens"]["spacing"], "palette": palette}
    if shape(o) != "kinds":
        return {"screens": front["screens"], "states": front["states"], **shared, "features": o["features"],
                "frequency": o["frequency"]}
    return {**shared, **{k: {"screens": one["front"]["screens"], "states": one["front"]["states"],
                             "features": one["features"], "frequency": one["frequency"]}
                         for k in kinds_of(o) for one in [as_kind({"oneshot": o}, k)["oneshot"]]}}


def build_front_text(spec: dict) -> str:
    o = spec["oneshot"]
    palette = next(p for p in o["front"]["tokens"]["palettes"] if p["id"] == o["r2"]["palette"])
    text = SHARED.sub(lambda m: m.group(2), oneshot.prompt(STAGE, shape(o)))
    if shape(o) == "kinds":
        text = for_kinds(text, kinds_of(o))
    text = text.replace("{tests_head}", o["tests_head"]).replace("{gate}", o["gate"]) \
        .replace("{contract}", "\n".join(f"- `{f}`" for f in o["contract"])) \
        .replace("{design}", json.dumps(designed(o, palette), ensure_ascii=False, indent=2))
    if "{viewports}" in text:   # a web app's, alone or among the kinds
        text = text.replace("{viewports}", ", ".join(views(o["profile"], "webapp" if shape(o) == "kinds" else "")))
    text = text.replace("{profile}", environment(o)).replace("{tools}", oneshot_build.tools_text(o)) \
        .replace("{window}", o["profile"].get("window", ""))
    if "android" not in kinds_of(o):
        return text
    if shape(o) == "kinds":   # the Android parts read only what applies to the Android app
        spec, o = (one := as_kind(spec, "android")), one["oneshot"]
    size = DEVICES[o["profile"]["device"]]
    return text.replace("{toolchain}", oneshot_build.toolchain_text(spec)) \
        .replace("{device}", f"{size['width']}×{size['height']} px at {size['dpi']} dpi") \
        .replace("{scenarios}", "\n".join(f"- `{r}`" for r in oneshot_device.scenarios(spec)) or "- (none)") \
        .replace("{owed}", "\n".join(f"- `{x['id']}` ({x['requirement']}, `{x['location']}`): {x['check']} — {x['reason']}"
                                     for x in oneshot_device.owed(spec)) or "- (none)") \
        .replace("{permissions}", ", ".join(f"`{p}`" for p in oneshot_device.grants(spec)) or "none") \
        .replace("{suffix}", oneshot_device.suffix(o)) \
        .replace("{fixture}", "yes" if oneshot_device.reads_screen(spec) else "no")


def begin(repo: str) -> None:
    """After R2's approval, which made the screens the spec's `done`: the build turn."""

    oneshot_build.send(repo, STAGE, build_front_text(oneshot_spec.spec_of(repo)))


def built_front(path: Path, run, spec: dict, final: str) -> str:
    """The report met, the contract pinned, the adapter filled and still running the backend's tests, the gate
    green on a clean HEAD, every planned test passing, and the app shot with no high finding."""

    o = spec["oneshot"]
    if why := oneshot_build.reported(spec, final) or oneshot_build.pinned(path, o):
        return why
    slots = slots_for(None, path)
    gate, live, backend = slots.get("gate_cmd", "").strip(), slots.get("live_cmd", "").strip(), o["gate"]
    if why := oneshot_build.published(gate, backend):
        return why
    kinds = kinds_of(o)
    android, several = "android" in kinds, shape(o) == "kinds"
    # Several kinds' gate adds each kind's tests and build to the core's: at least one command for each kind.
    # Counted where the gate's check cuts it (`chained`): on Windows a `&&` inside a quoted argument is no command.
    added = len(oneshot_build.commands(gate)) - len(oneshot_build.commands(backend))
    if several and added < len(kinds):
        return (f"여러 종류의 `gate_cmd` 는 핵심 테스트 `{backend}` 뒤에 종류마다의 테스트와 빌드 명령을 ` && ` 로 "
                f"더한다 ({', '.join(KIND_NAMES[k] for k in kinds)}) — `{gate}`")
    if android:   # an app on a phone has no `live_cmd`: its block names the APKs the emulator installs
        phone = as_kind(spec, "android") if several else spec
        device = oneshot_device.declared(path, final, oneshot_device.reads_screen(phone), oneshot_device.suffix(o))
    # A PC app's block names the program its look starts, apart from the installer S8 attaches.
    desktop = oneshot_desktop.declared(path, final) if "desktop" in kinds else None
    if "webapp" in kinds and not live:
        return "어댑터의 `live_cmd` 가 비었다 — 백엔드와 화면을 함께 띄우는 명령을 적는다"
    # The backend's own gate runs as the server knows it: the adapter's naming it proves nothing it runs. Several
    # kinds' gate builds the Android app on the core, so it runs on the Android toolchain. A PC app's gate starts
    # each program by the path its check chose.
    try:
        commands = [oneshot_build.run_as(c, o, path) for c in ([backend, gate] if gate != backend else [gate])]
    except ValueError as exc:
        return str(exc)
    verdict = specs.judge(path, commands, run.halt, lambda text: specs.note(run, text), env=oneshot_build.built_env(spec),
                          hide=oneshot_needs.hidden(spec))
    if not verdict["ok"]:
        return f"게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := oneshot_build.held(path, run, spec, verdict):
        return why
    if android and (why := oneshot_device.built(path, device)):
        return why
    store = oneshot_spec.store(spec["repo"])
    build = None
    if desktop is not None:   # frozen before any look: every look of the PC app launches that copy
        build, why = froze(path, spec, desktop, verdict["head"], run)
        if why:
            return why
    n = len(o.get("looks") or []) + 1
    stopped = lambda: run.halt.is_set() or runtime.stopping.is_set()   # noqa: E731
    shots = store.folder / "build" / f"r{n}"
    shutil.rmtree(shots, ignore_errors=True)
    findings, apks = [], {}
    for k in kinds:   # in the owner's order; a kind that fails sends the build back before the next is looked at
        one = as_kind(spec, k) if several else spec
        specs.note(run, f"{'에뮬레이터에 앱을 설치해' if k == 'android' else '앱을 띄워'} 화면을 찍는다 · {n}회차"
                   + (f" · {KIND_NAMES[k]}" if several else ""))
        try:
            found = looked(path, k, one, live, device if k == "android" else None, shots / k if several else shots,
                           stopped, apks, build)
        except OSError as exc:
            if k != "android":
                raise
            return f"게이트 뒤의 APK 를 복사하지 못했다 — {exc}"
        if several:   # each kind shot apart, then side by side: screen ids are unique across kinds
            for picture in (shots / k).glob("*.png"):
                picture.replace(shots / picture.name)
            shutil.rmtree(shots / k, ignore_errors=True)
            found = [{**f, "kind": k} for f in found]
        findings += found
        if stopped() or any(f["severity"] == "high" for f in found):
            break
    if stopped():   # a look cut short passes nothing: the stage stays `build-front`, resumable
        return "화면 검사 중에 멈췄다 — [재개] 로 빌드부터 다시 검사한다"
    # The app ran the tree's code, `live_cmd` may build: what was shot must still be the HEAD the gate passed.
    if (now := oneshot_build.head(path)) != verdict["head"] or specs.dirty(path):
        return (f"앱을 띄워 찍는 동안 작업트리가 바뀌었다 — HEAD `{now[:12]}`, 게이트는 `{verdict['head'][:12]}`. "
                "`live_cmd` 와 앱은 추적하는 파일을 바꾸지 않는다")
    looks = [*(o.get("looks") or []), {"round": n, "head": verdict["head"], "findings": findings,
                                       **({"build": build["identity"]} if build else {})}]
    if high := [f for f in findings if f["severity"] == "high"]:
        oneshot_spec.saved(spec["repo"], oneshot={"looks": looks})
        return "화면 검사에서 고칠 것이 나왔다 — " + " / ".join(
            (f"{KIND_NAMES[f['kind']]} " if "kind" in f else "") + f"{f['screen']} {f['viewport']} {f['state']}: {f['what']}"
            for f in high[:12])
    # `working` names the critique before its worker starts: a server that dies in between leaves it for `recover`.
    # The fixes this turn was sent with are built now: a resumed build no longer carries them. A run this version
    # holds at the critique (a PC app's critic is 9e4b's) is saved stopped in the same write, so no poll sees it
    # resumable.
    passed = {"stage": "critique", "front_built_head": verdict["head"], "looks": looks, "attempts": 0,
              "working": "critique", "fix": None, **({"device": device, "apks": apks} if android else {})}
    if oneshot_spec.held({**o, "stage": "critique"}):
        passed.update(working=None, stopped=oneshot_spec.kind_stop("critique"))
    oneshot_spec.saved(spec["repo"], oneshot=passed)
    specs.note(run, f"화면 빌드 통과 · {verdict['head'][:12]}")
    return ""


def froze(path: Path, spec: dict, desktop: dict, head: str, run) -> tuple[dict | None, str]:
    """The PC app's program, checked as the gate left it and frozen into staging the run owns: its identity is
    what every look launches. The build saved, or why there is none. An earlier frozen build is removed once
    this one is saved."""

    if why := oneshot_desktop.built(path, desktop):
        return None, why
    staging = oneshot_spec.store(spec["repo"]).folder / "desktop"
    into = staging / f"build-{time.time_ns()}"
    try:
        build = {**oneshot_desktop.frozen(path, desktop, into), "head": head}
    except BaseException:
        shutil.rmtree(into, ignore_errors=True)
        raise
    if oneshot_build.head(path) != head or specs.dirty(path):
        shutil.rmtree(into, ignore_errors=True)
        return None, f"PC 앱 빌드를 얼리는 동안 작업트리가 바뀌었다 — 게이트는 `{head[:12]}` 를 통과했다"
    try:
        oneshot_spec.saved(spec["repo"], oneshot={"desktop_build": build})
    except BaseException:   # a save may fail after it wrote: the record, read back, says whether the copy is kept
        try:
            kept = (oneshot_spec.spec_of(spec["repo"])["oneshot"].get("desktop_build") or {}).get("folder")
        except Exception:
            kept = build["folder"]   # unread: kept, for the next frozen build to remove
        if kept != build["folder"]:
            shutil.rmtree(into, ignore_errors=True)
        raise
    for old in staging.iterdir():
        if old != into:
            shutil.rmtree(old, ignore_errors=True)
    specs.note(run, f"PC 앱 빌드 통과 · {head[:12]} · 빌드 {build['identity'][:12]} · 파일 {build['files']}개")
    return build, ""


def looked(path: Path, kind: str, spec: dict, live: str, device: dict | None, shots: Path, stopped,
           apks: dict, build: dict | None = None) -> list[dict]:
    """One kind's look at the HEAD the gate passed, shot into `shots`: the web app served and shot in a browser,
    the Android app's APKs copied and installed on the run's emulator (`apks` gets the copies' digests), or the
    PC app's frozen `build` started on a desktop the run owns."""

    o, store = spec["oneshot"], oneshot_spec.store(spec["repo"])
    if kind == "desktop":
        return oneshot_desktop_look.look(spec, build, shots, stopped)
    if kind != "android":
        # The web app runs with the owner's secrets, and its text the look keeps shows `[N1]` for each.
        return oneshot_look.look(path, live, o["front"], o["frequency"], o["inventory"], views(o["profile"]), shots,
                                 stopped, env=oneshot_needs.gate_env(spec, None), hide=oneshot_needs.hidden(spec))
    # The emulator installs copies, and their digests are what the phone round must install again.
    copies = store.folder / "apks" / f"look-{time.time_ns()}"
    try:
        apks.update(oneshot_device.frozen(path, device, copies))
        return oneshot_device.look(copies, device, spec, store.folder / "device", shots, stopped)
    finally:
        shutil.rmtree(copies, ignore_errors=True)


oneshot_build.STAGES[STAGE] = (built_front, "gate", build_front_text, lambda repo: None)   # the critic follows (4c)
oneshot_build.GRANTS[STAGE] = lambda spec: True
oneshot_spec.TURNS.add(STAGE)
