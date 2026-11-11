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

KINDS = ("webapp", "api", "cli", "desktop", "data")
SUPPORTED_KINDS = ("webapp",)

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
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
# The stack is the owner's, picked in S2 (`docs/plans/oneshot/6-free-stack.md`); the card holds none of it.
PARTS = ("backend", "frontend", "styling", "data_store", "tests", "deploy")
STATUSES = ("confirmed", "assumed", "undecided")


def current(profile: dict | None) -> dict | None:
    """A card's values in today's schema: a field a later version dropped is no longer the card's."""

    return None if profile is None else {k: v for k, v in profile.items() if k in SCHEMA}


def proposal(value) -> dict:
    """The model's `oneshot-profile` block, checked against the schema: `{field: {value, why}}`."""

    if not isinstance(value, dict):
        raise ValueError("`oneshot-profile` 는 필드별 객체여야 한다")
    out = {}
    for name, item in value.items():
        field = SCHEMA.get(name)
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


def problems(profile: dict) -> tuple[list[str], list[str]]:
    """`(errors, warnings)` for the card. Errors keep it from being confirmed."""

    errors, warnings = [], []
    for name, field in SCHEMA.items():
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


def stacked(value) -> list[dict]:
    """The `stack` list of an `oneshot-spec` block, checked: `[{part, choice, why, status}]`, each part at
    most once. A part may still be missing; the conversation ends only when every part is confirmed."""

    if not isinstance(value, list):
        raise ValueError("`stack` 는 목록이어야 한다")
    out, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or item.get("part") not in PARTS:
            raise ValueError(f"`stack` 의 `part` 는 {', '.join(PARTS)} 중 하나다")
        if item["part"] in seen:
            raise ValueError(f"`stack` 에 `{item['part']}` 가 둘 이상이다")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("choice", "why")):
            raise ValueError(f"`stack` 의 `{item['part']}` 에 `choice` 와 `why` 가 있어야 한다")
        if item.get("status") not in STATUSES:
            raise ValueError(f"`stack` 의 `status` 는 {', '.join(STATUSES)} 중 하나다")
        seen.add(item["part"])
        out.append({"part": item["part"], "choice": item["choice"].strip(), "why": " ".join(item["why"].split()),
                    "status": item["status"]})
    return sorted(out, key=lambda s: PARTS.index(s["part"]))


def unsettled(stack: list[dict]) -> list[str]:
    """The parts the owner has not confirmed yet, in the plan's order."""

    confirmed = {s["part"] for s in stack if s["status"] == "confirmed"}
    return [p for p in PARTS if p not in confirmed]


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
