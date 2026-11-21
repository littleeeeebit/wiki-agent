"""DeepSeek V4.1 Flash, run by the Claude CLI against DeepSeek's Anthropic-compatible endpoint.

The CLI stays the same and only where it sends requests, and with which key, changes. So every harness the app
gives Claude (tools, hooks, permission prompts, `--resume`) runs unchanged. DeepSeek honours `output_config.effort`
at `low`, `high` and `max`. Without a level it thinks at `high`, so the app asks for `max` unless one is chosen.
The key is the hub's `.env` `DEEPSEEK_API_KEY`, and only this process's loopback `Relay` holds it. The CLI's endpoint
is the relay and its key a per-process token, read through `apiKeyHelper` (`tool/deepseek_key.py`), so neither is in
the environment the CLI's tools and hooks inherit. The endpoint, the key helper and the model names go in
`--settings`, which outranks the user's, the project's and the local settings. Managed policy outranks `--settings`
and may still send a turn elsewhere, but what it carries there is the token, never the DeepSeek key.
"""

from __future__ import annotations

import atexit
from pathlib import Path
import re
import sys
import threading

from common import settings

from .deepseek_relay import Relay

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
         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
         "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD")
# A shell runs the helper: these would expand or split inside its double quotes.
SHELL = re.compile(r'[%!$`"^&|<>]')
_relay: Relay | None = None
_relay_lock = threading.Lock()


def relay() -> Relay:
    """This process's relay, started on first use. The app server closes it on shutdown, a standalone caller with
    `close()`, and process exit as the last resort."""

    global _relay
    with _relay_lock:
        if _relay is None:
            _relay = Relay(URL, settings.HUB / ".env")
            atexit.register(close)
        return _relay


def close() -> None:
    global _relay
    with _relay_lock:
        gone, _relay = _relay, None
    if gone is not None:
        gone.close()


def built(given: dict, url: str, env: Path) -> dict:
    helper = Path(__file__).resolve().parents[1] / "deepseek_key.py"
    paths = (sys.executable, str(helper), str(env))
    if any(SHELL.search(p) for p in paths):
        raise RuntimeError(f"DeepSeek 키 도우미 경로에 셸 문자가 있다: {' · '.join(paths)}")
    return {**given, "apiKeyHelper": " ".join(f'"{p}"' for p in paths),
            "env": {"ANTHROPIC_BASE_URL": url, **dict.fromkeys(ROLES, MODEL),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **dict.fromkeys(ROUTE, "")}}


def flags(model: str | None, given: dict) -> dict:
    """The `--settings` JSON: `given` itself, or for DeepSeek with this process's relay as the endpoint, its token
    from the key helper, and the model names."""

    if model != MODEL:
        return given
    if not settings.entries(settings.HUB / ".env").get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
    held = relay()
    return built(given, held.url, held.folder / ".env")


def row_provider(row: dict) -> str | None:
    """Whose turn a saved row records, or None when it does not say: `deepseek-flash` is DeepSeek's whatever the
    row's own label, since rows from before DeepSeek had its own provider name call it `claude`."""

    return "deepseek" if row.get("model") == MODEL else row.get("provider")


def environment(model: str | None, base: dict) -> dict:
    """`base` without a credential for DeepSeek's turns, else `base` itself. A Claude login or key would beat the
    key helper and send the turn to Anthropic; the DeepSeek key comes from the helper alone."""

    if model != MODEL:
        return base
    dropped = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "DEEPSEEK_API_KEY"}
    return {k: v for k, v in base.items() if k not in dropped}
