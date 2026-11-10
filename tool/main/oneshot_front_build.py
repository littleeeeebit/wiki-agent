"""oneshot S7's build: one work turn builds the approved screens, the server checks it and shoots it.

`docs/plans/oneshot/4-frontend.md` ("S7 — Build") is the design. The turn runs as the backend's did
(`oneshot_build.send`), and only while it runs may it write the worktree's `.wiki/adapter.toml`: it fills
`live_cmd` and widens the gate. The check reads the committed adapter back, runs the gate, keeps the
contract tests pinned and passing, then starts the app and shoots it (`oneshot_look`). A high finding
goes back to the turn. A pass leaves the run at `critique`, where the critic takes it on.
"""

from __future__ import annotations

import json
from pathlib import Path

from wiki import slots_for

from . import oneshot_build, oneshot_look, oneshot_screens as screens, oneshot_spec, runtime, specs
from .query import ROOT

BUILD_FRONT_PROMPT = (ROOT / "tool/prompts/oneshot-build-front.md").read_text(encoding="utf-8")
STAGE = "build-front"


def views(profile: dict) -> list[str]:
    return list(screens.VIEWPORTS) if profile.get("responsive") == "desktop-mobile" else ["desktop"]


def build_front_text(spec: dict) -> str:
    o = spec["oneshot"]
    front = {k: v for k, v in o["front"].items() if k not in ("mocks", "shots")}
    palette = next(p for p in o["front"]["tokens"]["palettes"] if p["id"] == o["r2"]["palette"])
    return BUILD_FRONT_PROMPT.replace("{tests_head}", o["tests_head"]).replace("{gate}", o["gate"]) \
        .replace("{contract}", "\n".join(f"- `{f}`" for f in o["contract"])) \
        .replace("{design}", json.dumps({"screens": front["screens"], "states": front["states"],
                                         "type": front["tokens"]["type"], "spacing": front["tokens"]["spacing"],
                                         "palette": palette, "features": o["features"],
                                         "frequency": o["frequency"]}, ensure_ascii=False, indent=2)) \
        .replace("{viewports}", ", ".join(views(o["profile"]))).replace("{profile}", json.dumps(o["profile"]))


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
    if backend not in gate:
        return f"어댑터의 `gate_cmd` 가 백엔드 테스트 `{backend}` 를 잃었다 — `{gate}`"
    if not live:
        return "어댑터의 `live_cmd` 가 비었다 — 백엔드와 화면을 함께 띄우는 명령을 적는다"
    # The backend's own gate runs as the server knows it: the adapter's naming it proves nothing it runs.
    verdict = specs.judge(path, [backend, gate] if gate != backend else [gate], run.halt,
                          lambda text: specs.note(run, text))
    if not verdict["ok"]:
        return f"게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := oneshot_build.held(path, run, spec, verdict):
        return why
    store = oneshot_spec.store(spec["repo"])
    n = len(o.get("looks") or []) + 1
    specs.note(run, f"앱을 띄워 화면을 찍는다 · {n}회차")
    stopped = lambda: run.halt.is_set() or runtime.stopping.is_set()   # noqa: E731
    findings = oneshot_look.look(path, live, o["front"], o["frequency"], o["inventory"], views(o["profile"]),
                                 store.folder / "build" / f"r{n}", stopped)
    if stopped():   # a look cut short passes nothing: the stage stays `build-front`, resumable
        return "화면 검사 중에 멈췄다 — [재개] 로 빌드부터 다시 검사한다"
    # The app ran the tree's code, `live_cmd` may build: what was shot must still be the HEAD the gate passed.
    if (now := oneshot_build.head(path)) != verdict["head"] or specs.dirty(path):
        return (f"앱을 띄워 찍는 동안 작업트리가 바뀌었다 — HEAD `{now[:12]}`, 게이트는 `{verdict['head'][:12]}`. "
                "`live_cmd` 와 앱은 추적하는 파일을 바꾸지 않는다")
    looks = [*(o.get("looks") or []), {"round": n, "head": verdict["head"], "findings": findings}]
    if high := [f for f in findings if f["severity"] == "high"]:
        oneshot_spec.saved(spec["repo"], oneshot={"looks": looks})
        return "화면 검사에서 고칠 것이 나왔다 — " + " / ".join(
            f"{f['screen']} {f['viewport']} {f['state']}: {f['what']}" for f in high[:12])
    # `working` names the critique before its worker starts: a server that dies in between leaves it for `recover`.
    # The fixes this turn was sent with are built now: a resumed build no longer carries them.
    oneshot_spec.saved(spec["repo"], oneshot={"stage": "critique", "front_built_head": verdict["head"], "looks": looks,
                                              "attempts": 0, "working": "critique", "fix": None})
    specs.note(run, f"화면 빌드 통과 · {verdict['head'][:12]}")
    return ""


oneshot_build.STAGES[STAGE] = (built_front, "gate", build_front_text, lambda repo: None)   # the critic follows (4c)
oneshot_build.GRANTS.add(STAGE)
oneshot_spec.TURNS.add(STAGE)
