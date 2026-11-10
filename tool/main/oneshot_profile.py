"""The oneshot environment card's schema, its checks, the adapter's gate, and the run's spending ledger.

The schema is the server's, never the model's (`docs/plans/oneshot/2-bootstrap.md`, "S1").
Pure functions: no run records, no sessions.
"""

from __future__ import annotations

import re
import sys

from common import settings
from common.host import conda_exe

ESTIMATE_USD = 2.0   # a USD-metered role's first turn, before any sample; `.env` may override per role
TURN_SECONDS = 600.0  # a cut turn's time before the role has a longest turn on record

KINDS = ("webapp", "api", "cli", "desktop", "data")
SUPPORTED_KINDS = ("webapp",)

# The schema is the server's, never the model's: a field or option outside it refuses the block.
# `enabled` narrows what this version can run end to end; `asked` fields start empty on a kind's first run.
SCHEMA: dict[str, dict] = {
    "os_now": {"options": ["windows", "macos"], "default": "windows" if sys.platform == "win32" else "macos"},
    "os_later": {"options": ["none", "other"], "default": "other"},
    "python_env": {"options": ["uv", "venv", "conda"], "default": "uv"},
    "conda_env": {"text": True, "default": ""},
    "backend": {"options": ["fastapi", "node", "none"], "default": "fastapi", "enabled": ["fastapi"]},
    "frontend": {"options": ["vite-react", "jinja-htmx", "tauri", "none"], "default": "vite-react",
                 "enabled": ["vite-react"]},
    "styling": {"options": ["tailwind", "css", "component-library"], "default": "tailwind"},
    "data_store": {"options": ["sqlite", "json", "postgres"], "default": "sqlite"},
    "auth": {"options": ["none", "password", "oauth"], "default": "none"},
    "deploy": {"options": ["local", "docker", "cloud"], "default": "local"},
    "visibility": {"options": ["private", "public"], "default": "", "asked": True},
    "license": {"options": ["none", "mit", "apache-2.0"], "default": "none"},
    "ci": {"options": ["none", "github-actions"], "default": "none"},
    "js_pm": {"options": ["npm", "pnpm"], "default": "npm"},
    "ui_language": {"options": ["ko", "en", "both"], "default": "ko"},
    "docs_language": {"options": ["ko", "en"], "default": "", "asked": True},
    "responsive": {"options": ["desktop", "desktop-mobile"], "default": "desktop-mobile"},
    "llm": {"options": ["none", "anthropic", "openai", "other"], "default": "none"},
    "github_owner": {"text": True, "default": "", "asked": True},
    "budget_usd": {"number": True, "default": "", "asked": True},
    "budget_hours": {"number": True, "default": "", "asked": True},
}
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
CONDA_ENV = re.compile(r"[\w.-]{1,64}")

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
        if field.get("number") and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            raise ValueError(f"`{name}` 은 숫자여야 한다")
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
            if not (name == "conda_env" and profile.get("python_env") != "conda"):
                errors.append(f"`{name}` 을 채워야 한다")
            continue
        if "options" in field and v not in field["options"]:
            errors.append(f"`{name}` 에 없는 선택지다: {v}")
        elif "enabled" in field and v not in field["enabled"]:
            errors.append(f"`{name}` 의 `{v}` 는 이 버전에서 아직 고를 수 없다")
        if field.get("number") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
            errors.append(f"`{name}` 은 0보다 큰 숫자여야 한다")
    if profile.get("python_env") == "conda":
        if not CONDA_ENV.fullmatch(str(profile.get("conda_env") or "")):
            errors.append("conda 는 환경 이름이 있어야 한다 (영문·숫자·`._-`)")
        if not conda_exe():
            errors.append("이 PC 에서 conda 를 찾지 못했다")
    if profile.get("python_env") == "venv" and profile.get("os_later") == "other":
        warnings.append("stdlib venv 는 OS 마다 인터프리터 경로가 달라 하나의 gate 로 쓸 수 없다 — uv 를 권한다")
    if profile.get("visibility") == "public" and profile.get("license") == "none":
        errors.append("공개 저장소는 라이선스를 골라야 한다")
    if profile.get("visibility") == "private" and profile.get("ci") != "none":
        errors.append("비공개 저장소에는 CI 워크플로를 두지 않는다")
    owner = str(profile.get("github_owner") or "")
    if owner and not OWNER.fullmatch(owner):
        errors.append("GitHub owner 이름이 올바르지 않다")
    return errors, warnings


def gate_cmd(profile: dict) -> str:
    env = profile["python_env"]
    if env == "uv":
        return "uv run pytest"
    if env == "conda":
        return f'"{conda_exe()}" run --no-capture-output -n {profile["conda_env"]} python -m pytest'
    folder = "Scripts" if profile["os_now"] == "windows" else "bin"
    return f".venv/{folder}/python -m pytest"


def python_cmd(profile: dict) -> str:
    """The project's own interpreter, as `gate_cmd` reaches it."""

    return gate_cmd(profile).removesuffix(" -m pytest").removesuffix(" pytest") + (
        " python" if profile["python_env"] == "uv" else "")


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


def allowed(spend: dict, profile: dict | None, role: str, codex: bool) -> str:
    """Empty when the role may start a turn now, or the reason it may not.
    Ceilings are enforced between turns, never inside one."""

    if not profile:
        return ""   # before the card there is no ceiling: S0 and S1 only record
    spend = ledger(spend)
    if spend["seconds"] >= float(profile["budget_hours"]) * 3600:
        return "시간 한도를 다 썼다"
    if codex:
        return ""   # the subscription reports no money; hours bound it
    expected = spend["max"].get(role, estimate(role))
    if spend["usd"] + expected > float(profile["budget_usd"]):
        return f"금액 한도를 넘는다 — 쓴 돈 USD {spend['usd']:.2f}, 이번 턴 예상 USD {expected:.2f}"
    return ""


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
