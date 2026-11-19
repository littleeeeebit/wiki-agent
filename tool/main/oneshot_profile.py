"""The oneshot environment card's schema and its checks, the stack the owner picks in S2, and the run's
spending ledger.

The schema is the server's, never the model's (`docs/plans/oneshot/2-bootstrap.md`, "S1").
Pure functions: no run records, no sessions.
"""

from __future__ import annotations

import json
import re
import sys

from common import settings

ESTIMATE_USD = 2.0   # a USD-metered role's first turn, before any sample; `.env` may override per role
TURN_SECONDS = 600.0  # a cut turn's time before the role has a longest turn on record

KINDS = ("webapp", "android", "api", "cli", "desktop", "data")
SUPPORTED_KINDS = ("webapp", "android", "desktop")
# A kind only some hosts build, with why the others cannot: a PC app is built on Windows only in plan 9.
HOST_ONLY = {"desktop": ("win32", "PC 앱은 Windows 에서만 만든다")}
# The stage a kind waits at in this version, stopped with reason `kind` until its plan step lands. A kind not
# here runs to the end: `android` does since its R3 sheet and S8 (`docs/plans/oneshot/7-android.md`, 7f2), and
# several kinds without `desktop` since 9c4b. `desktop`, alone or among other kinds, runs its core design, R1,
# S4's tests and the core build, and waits before the backend review until 9e2c (`docs/plans/oneshot/9-kinds.md`,
# "Steps and their stops").
KIND_STOPS: dict[str, str] = {"desktop": "review"}
# A run's stages in order after the bootstrap (`web/src/lib/oneshot.ts` STAGES).
STAGE_ORDER = ("spec", "design", "r1", "tests", "build", "review", "split", "board", "frontend", "r2", "build-front",
               "critique", "phone", "r3", "pr", "done")
# The sizes S7 sets a PC app's main window to: its frame in physical pixels, as the driver reads it back.
WINDOWS = ("1280x800", "1024x768", "1440x900", "1920x1080")
# The reference phone S7 shoots (`device` replaces `responsive` for `android`): emulator hardware profiles.
DEVICES = {"medium-phone": {"label": "보통 폰", "width": 1080, "height": 2400, "dpi": 420},
           "galaxy-s21": {"label": "Galaxy S21", "width": 1080, "height": 2400, "dpi": 421},
           "small-phone": {"label": "작은 폰", "width": 720, "height": 1280, "dpi": 320}}

# The schema is the server's, never the model's: a field or option outside it refuses the block.
# `enabled` narrows what this version can run end to end; `asked` fields start empty on a kind's first run.
SCHEMA: dict[str, dict] = {
    "os_now": {"options": ["windows", "macos"], "default": "windows" if sys.platform == "win32" else "macos"},
    "os_later": {"options": ["none", "other"], "default": "other"},
    "auth": {"options": ["none", "password", "oauth"], "default": "none"},
    "visibility": {"options": ["private", "public"], "default": "", "asked": True},
    "license": {"options": ["none", "mit", "apache-2.0"], "default": "none"},
    "ci": {"options": ["none", "github-actions"], "default": "none"},
    "ui_language": {"options": ["ko", "en", "both"], "default": "ko"},
    "docs_language": {"options": ["ko", "en"], "default": "", "asked": True},
    "responsive": {"options": ["desktop", "desktop-mobile"], "default": "desktop-mobile"},
    "llm": {"options": ["none", "anthropic", "openai", "other"], "default": "none"},
    "github_owner": {"text": True, "default": "", "asked": True},
}
FIELDS = {*SCHEMA, "device", "window"}   # every kind's card fields
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")   # a program's name on PATH, never a path
PINNED = re.compile(r"[A-Za-z0-9-]+(?:/[A-Za-z0-9._-]+)+@[A-Za-z0-9._-]+")   # owner/repo[/path]@ref, no empty segment
# The stack is the owner's, picked in S2 (`docs/plans/oneshot/6-free-stack.md`); the card holds none of it.
PARTS = ("backend", "frontend", "styling", "data_store", "tests", "deploy")
ANDROID_PARTS = ("language", "ui", "core", "data_store", "tests", "build")
# A run of several kinds: the parts every kind shares once, then each kind's own as `<kind>.<part>` (plan 9b).
SHARED_PARTS = ("core", "data_store", "tests")
OWN_PARTS = {"webapp": ("backend", "frontend", "styling", "deploy"), "android": ("ui", "build"), "desktop": ("ui", "build")}
STATUSES = ("confirmed", "assumed", "undecided")


def kind_of(o: dict) -> str:
    """The run's kind, from its record or its spec's `oneshot` field: a record from before kinds is a web app's."""

    return (o.get("intent") or {}).get("kind", "webapp")


def kinds_of(o: dict) -> list[str]:
    """The owner's kinds, in the order picked: a record from before the owner picked them has its one `kind`."""

    return list((o.get("intent") or {}).get("kinds") or [kind_of(o)])


def waits_at(o: dict) -> str | None:
    """The stage the run waits at in this version, stopped with reason `kind`: the earliest any of its kinds
    waits at, so several kinds wait where `desktop` does."""

    stops = [KIND_STOPS[k] for k in kinds_of(o) if k in KIND_STOPS]
    return stops[0] if stops else None


def held(o: dict) -> bool:
    """Whether the run stands at or past the stage it waits at in this version, so nothing may start it: a run an
    earlier version carried past today's stop (several kinds with `desktop` at `frontend`) stays held as well."""

    stop = waits_at(o)
    return stop is not None and o.get("stage") in STAGE_ORDER and STAGE_ORDER.index(o["stage"]) >= STAGE_ORDER.index(stop)


def unavailable() -> dict[str, str]:
    """The supported kinds this host cannot build, each with why, in the owner's words."""

    return {k: why for k, (host, why) in HOST_ONLY.items() if sys.platform != host}


def off_host(o: dict) -> str | None:
    """Why this host cannot go on with the run `o` (a run, or a spec's `oneshot`), or None: the owner's kinds as
    picked at the start (a run's own `kinds`, before any restatement) and as restated (`intent`)."""

    picked = {*(o.get("kinds") or []), *kinds_of(o)}
    return next((why for k, why in unavailable().items() if k in picked), None)


def shape(o: dict) -> str:
    """Which stage instructions and checks a run takes: its one kind, or `kinds` for several sharing one core."""

    return "kinds" if len(kinds_of(o)) > 1 else kind_of(o)


def _kinds(kind: str | list[str]) -> list[str]:
    return [kind] if isinstance(kind, str) else list(kind)


def parts(kind: str | list[str] = "webapp") -> tuple[str, ...]:
    """The stack's parts for one kind, or for several: the shared parts, then each kind's own."""

    kinds = _kinds(kind)
    if len(kinds) > 1:
        return (*SHARED_PARTS, *(f"{k}.{p}" for k in kinds for p in OWN_PARTS[k]))
    return ANDROID_PARTS if kinds[0] in ("android", "desktop") else PARTS   # a PC app alone takes Android's six


def schema(kind: str | list[str] = "webapp") -> dict[str, dict]:
    """The card's fields for `kind`: an `android` card names its reference phone, and a `desktop` card the size
    of its main window, instead of web viewports. Several kinds' card holds every field any of them has, each once."""

    if len(kinds := _kinds(kind)) > 1:
        fields = {k: f for one in kinds for k, f in schema(one).items()}
    elif own := {"android": ("device", {"options": list(DEVICES), "default": "medium-phone"}),
                 "desktop": ("window", {"options": list(WINDOWS), "default": WINDOWS[0]})}.get(kinds[0]):
        fields = {(own[0] if k == "responsive" else k): (own[1] if k == "responsive" else f) for k, f in SCHEMA.items()}
    else:
        return SCHEMA
    # A PC app is built and checked on this Windows PC (plan 9e): its card, alone or among kinds, names no other host.
    return {**fields, "os_now": {"options": ["windows"], "default": "windows"}} if "desktop" in kinds else fields


def current(profile: dict | None) -> dict | None:
    """A card's values in today's schema: a field a later version dropped is no longer the card's."""

    return None if profile is None else {k: v for k, v in profile.items() if k in FIELDS}


def proposal(value, kind: str = "webapp") -> dict:
    """The model's `oneshot-profile` block, checked against the kind's schema: `{field: {value, why}}`."""

    if not isinstance(value, dict):
        raise ValueError("`oneshot-profile` 는 필드별 객체여야 한다")
    out, fields = {}, schema(kind)
    for name, item in value.items():
        field = fields.get(name)
        if field is None:
            raise ValueError(f"스키마에 없는 필드다: {name}")
        if not isinstance(item, dict) or not isinstance(item.get("why"), str) or "value" not in item:
            raise ValueError(f"`{name}` 에 value 와 why 가 있어야 한다")
        if field.get("asked"):
            raise ValueError(f"`{name}` 은 사람이 정한다")
        v = item["value"]
        if "options" in field and v not in field.get("enabled", field["options"]):
            raise ValueError(f"`{name}` 에 고를 수 없는 선택지다: {v}")
        if field.get("text") and not isinstance(v, str):
            raise ValueError(f"`{name}` 은 문자열이어야 한다")
        out[name] = {"value": v, "why": item["why"].strip()}
    return out


def problems(profile: dict, kind: str = "webapp") -> tuple[list[str], list[str]]:
    """`(errors, warnings)` for the card. Errors keep it from being confirmed."""

    errors, warnings = [], []
    for name, field in schema(kind).items():
        v = profile.get(name, "")
        if v in ("", None):
            errors.append(f"`{name}` 을 채워야 한다")
            continue
        if "options" in field and v not in field["options"]:
            errors.append(f"`{name}` 에 없는 선택지다: {v}")
        elif "enabled" in field and v not in field["enabled"]:
            errors.append(f"`{name}` 의 `{v}` 는 이 버전에서 아직 고를 수 없다")
    if profile.get("visibility") == "public" and profile.get("license") == "none":
        errors.append("공개 저장소는 라이선스를 골라야 한다")
    if profile.get("visibility") == "private" and profile.get("ci") != "none":
        errors.append("비공개 저장소에는 CI 워크플로를 두지 않는다")
    owner = str(profile.get("github_owner") or "")
    if owner and not OWNER.fullmatch(owner):
        errors.append("GitHub owner 이름이 올바르지 않다")
    return errors, warnings


def tooled(part: str, kind: str | list[str]) -> bool:
    """Whether `part` is a PC app's `build`, which names the host tools its stack runs (plan 9e, "The toolchain,
    declared once"): `build` for `desktop` alone, `desktop.build` among several kinds."""

    kinds = _kinds(kind)
    return part == ("build" if kinds == ["desktop"] else "desktop.build" if "desktop" in kinds else None)


def tool_key(name: str) -> str:
    """The one name a declared tool and a command's program are compared by: lowercased, an executable's
    extension set aside, so `npm`, `npm.cmd` and `NPM.CMD` are one program."""

    return re.sub(r"\.(exe|cmd|bat|ps1|com)$", "", name.lower())


def tools_of(value, ci: bool) -> list[dict]:
    """A PC app's `tools`, checked: `[{name, setup}]`, each `name` a program to find on `PATH` and each once.
    `setup` is how a clean CI runner gets it: `preinstalled` on the `windows-latest` image, a `run` command, or an
    action pinned to a version (`uses: owner/action@ref`) with string `with` inputs. It is required when the
    card's CI is on."""

    if not isinstance(value, list) or not value:
        raise ValueError("PC 앱의 `build` 에는 `tools` 목록이 있어야 한다 — 스택이 쓰는 프로그램마다 하나씩")
    out, seen = [], set()
    for tool in value:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str) or not TOOL.fullmatch(name.strip()):
            raise ValueError("`tools` 의 `name` 은 PATH 에서 찾을 프로그램 이름이다 — 경로가 아니다")
        name = name.strip()
        if tool_key(name) in seen:
            raise ValueError(f"`tools` 에 `{name}` 이 둘 이상이다 — 확장자만 다른 이름도 같은 프로그램이다")
        seen.add(tool_key(name))
        setup = tool.get("setup")
        if setup is None and not ci:
            out.append({"name": name})
            continue
        if setup == "preinstalled" or isinstance(setup, dict) and (
                set(setup) == {"run"} and isinstance(setup["run"], str) and setup["run"].strip()
                or set(setup) <= {"uses", "with"} and isinstance(setup.get("uses"), str) and PINNED.fullmatch(setup["uses"])
                and isinstance(inputs := setup.get("with", {}), dict)   # inputs as Actions reads them: text only,
                and all(isinstance(k, str) and isinstance(v, str) for k, v in inputs.items())):   # shown as stored
            out.append({"name": name, "setup": setup})
            continue
        raise ValueError(f"`{name}` 의 `setup` 은 `preinstalled`, `{{\"run\": …}}`, 또는 버전을 고정한 "
                         f"`{{\"uses\": \"owner/action@ref\"}}` 다" + (" — CI 를 쓰는 카드는 도구마다 필요하다" if ci else ""))
    return out


def stacked(value, kind: str = "webapp", ci: bool = False) -> list[dict]:
    """The `stack` list of an `oneshot-spec` block, checked: `[{part, choice, why, status}]`, each of the kind's
    parts at most once, and a PC app's `build` with its `tools`. A part may still be missing; the conversation
    ends only when every part is confirmed."""

    if not isinstance(value, list):
        raise ValueError("`stack` 는 목록이어야 한다")
    out, seen, names = [], set(), parts(kind)
    for item in value:
        if not isinstance(item, dict) or item.get("part") not in names:
            raise ValueError(f"`stack` 의 `part` 는 {', '.join(names)} 중 하나다")
        if item["part"] in seen:
            raise ValueError(f"`stack` 에 `{item['part']}` 가 둘 이상이다")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("choice", "why")):
            raise ValueError(f"`stack` 의 `{item['part']}` 에 `choice` 와 `why` 가 있어야 한다")
        if item.get("status") not in STATUSES:
            raise ValueError(f"`stack` 의 `status` 는 {', '.join(STATUSES)} 중 하나다")
        seen.add(item["part"])
        tools = {"tools": tools_of(item.get("tools"), ci)} if tooled(item["part"], kind) else {}
        if "tools" in item and not tools:
            raise ValueError(f"`tools` 는 PC 앱의 `build` 에만 있다 — `{item['part']}` 에는 없다")
        out.append({"part": item["part"], "choice": item["choice"].strip(), "why": " ".join(item["why"].split()),
                    "status": item["status"], **tools})
    return sorted(out, key=lambda s: names.index(s["part"]))


def unsettled(stack: list[dict], kind: str = "webapp") -> list[str]:
    """The kind's parts the owner has not confirmed yet, in the plan's order."""

    confirmed = {s["part"] for s in stack if s["status"] == "confirmed"}
    return [p for p in parts(kind) if p not in confirmed]


def environment(o: dict) -> str:
    """What a turn is told about the project's setting: the card and the stack the owner picked in S2."""

    return json.dumps({**current(o["profile"]), "stack": {s["part"]: s["choice"] for s in o.get("stack") or []}},
                      ensure_ascii=False)


# -- Spending -------------------------------------------------------------------------

def estimate(role: str) -> float:
    found = settings.entries(settings.HUB / ".env")
    for key in (f"ONESHOT_ESTIMATE_USD_{role.upper()}", "ONESHOT_ESTIMATE_USD"):
        try:
            return float(found[key])
        except (KeyError, ValueError):
            continue
    return ESTIMATE_USD


def ledger(spend: dict | None) -> dict:
    return {"usd": 0.0, "seconds": 0.0, "calls": 0, "unknown": 0, "max": {}, "longest": {}, **(spend or {})}


def charged(spend: dict, role: str, codex: bool, cost, seconds: float) -> dict:
    spend = ledger(spend)
    spend["seconds"] = round(spend["seconds"] + seconds, 1)
    spend["longest"] = {**spend["longest"], role: max(seconds, spend["longest"].get(role, 0.0))}
    spend["calls"] += 1
    if codex:
        return spend
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        spend["usd"] = round(spend["usd"] + cost, 6)
        spend["max"] = {**spend["max"], role: max(cost, spend["max"].get(role, 0.0))}
    else:
        spend["usd"] = round(spend["usd"] + max(estimate(role), spend["max"].get(role, 0.0)), 6)
        spend["unknown"] += 1
    return spend


def cut(spend: dict, role: str, codex: bool, elapsed: float, lived: float) -> dict:
    """A turn the server died inside, charged once: its cost as unreported. Its time is at least
    `lived`, the span its heartbeats prove, and otherwise the larger of the role's longest turn and
    `TURN_SECONDS`, never more than the wall-clock time since it was sent."""

    spend = ledger(spend)
    seconds = max(lived, min(elapsed, max(TURN_SECONDS, spend["longest"].get(role, 0.0))))
    out = charged(spend, role, codex, None, seconds)
    if codex:
        out["unknown"] += 1   # `charged` marks only USD roles; a Codex turn's time is the unknown here
    out["longest"] = spend["longest"]   # an estimate is not a measured turn
    return out
