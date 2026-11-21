"""DeepSeek V4.1 Flash, run by the Claude CLI against DeepSeek's Anthropic-compatible endpoint.

The CLI stays the same and only where it sends requests, and with which key, changes. So every harness the app
gives Claude (tools, hooks, permission prompts, `--resume`) runs unchanged. DeepSeek honours `output_config.effort`
at `low`, `high` and `max`. Without a level it thinks at `high`, so the app asks for `max` unless one is chosen.
The key is the hub's `.env` `DEEPSEEK_API_KEY`. The CLI reads it through `apiKeyHelper` (`tool/deepseek_key.py`), so it
is never in the environment the CLI's tools and hooks inherit. The endpoint, the key helper and the model names go in
`--settings`, which outranks the user's, the project's and the local settings: none of them can send the turn, or the
key, somewhere else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from common import settings

MODEL = "deepseek-flash"
URL = "https://api.deepseek.com/anthropic"
EFFORTS = [{"id": "", "label": "기본 (max)", "note": ""}] + [
    {"id": e, "label": e, "note": ""} for e in ("low", "high", "max")]
CHOICE = {"id": MODEL, "label": "DeepSeek Flash", "note": "DeepSeek V4.1 Flash · 기본 max", "efforts": EFFORTS}
# The CLI's own side calls (titles, summaries, subagents) name a Claude model; on DeepSeek's endpoint they go here.
ROLES = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
         "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")


def effort(model: str | None, chosen: str | None) -> str | None:
    return chosen or ("max" if model == MODEL else None)


def cost(model: str | None, result: dict) -> float | None:
    """A turn's price in dollars. The CLI reckons it at a Claude model's rates, wrong for DeepSeek's, so none."""

    return None if model == MODEL else result.get("total_cost_usd")


def provider(model: str | None) -> str:
    """Whose conversation a model's turns are: a session id from one provider means nothing to another."""

    return "codex" if (model or "").startswith("codex:") else "deepseek" if model == MODEL else "claude"


def of(chat) -> str:
    """The provider a live session's turns go to."""

    return "codex" if chat.is_codex else provider(chat.model)


# What would carry a turn, or a credential, past the endpoint and key helper set here. An empty value reads as
# unset, so the flag settings blank each one a user's or project's settings may hold (probed with a stand-in).
ROUTE = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_OAUTH_TOKEN",
         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS")
# A shell runs the helper: these would expand or split inside its double quotes.
SHELL = re.compile(r'[%!$`"^&|<>]')


def managed() -> list[dict]:
    """Every managed settings object on this machine. They outrank `--settings`, so none may route the CLI."""

    if os.name == "nt":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode"]
    else:
        roots = [Path("/Library/Application Support/ClaudeCode"), Path("/etc/claude-code")]
    files = [p for root in roots for p in [root / "managed-settings.json", *sorted(root.glob("managed-settings.d/*.json"))]]
    found = [json.loads(p.read_text(encoding="utf-8")) for p in files if p.is_file()]
    if os.name == "nt":
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Policies\ClaudeCode") as key:
                    found.append(json.loads(winreg.QueryValueEx(key, "Settings")[0]))
            except OSError:
                pass
    return found


def flags(model: str | None, given: dict) -> dict:
    """The `--settings` JSON: `given` itself, or for DeepSeek with its endpoint, key helper and model names."""

    if model != MODEL:
        return given
    env = settings.HUB / ".env"
    if not settings.entries(env).get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
    helper = Path(__file__).resolve().parents[1] / "deepseek_key.py"
    paths = (sys.executable, str(helper), str(env))
    if any(SHELL.search(p) for p in paths):
        raise RuntimeError(f"DeepSeek 키 도우미 경로에 셸 문자가 있다: {' · '.join(paths)}")
    for policy in managed():
        if policy.get("apiKeyHelper") or set(policy.get("env") or {}) & {"ANTHROPIC_BASE_URL", *ROUTE}:
            raise RuntimeError("관리 설정이 Claude CLI 의 엔드포인트나 인증을 정한다 — DeepSeek 은 이 PC 에서 쓰지 않는다")
    return {**given, "apiKeyHelper": " ".join(f'"{p}"' for p in paths),
            "env": {"ANTHROPIC_BASE_URL": URL, **dict.fromkeys(ROLES, MODEL),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **dict.fromkeys(ROUTE, "")}}


def environment(model: str | None, base: dict) -> dict:
    """`base` without a credential for DeepSeek's turns, else `base` itself. A Claude login or key would beat the
    key helper and send the turn to Anthropic; the DeepSeek key comes from the helper alone."""

    if model != MODEL:
        return base
    dropped = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "DEEPSEEK_API_KEY"}
    return {k: v for k, v in base.items() if k not in dropped}
