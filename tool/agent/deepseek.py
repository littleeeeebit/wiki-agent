"""DeepSeek V4.1 Flash, run by the Claude CLI against DeepSeek's Anthropic-compatible endpoint.

The CLI stays the same and only where it sends requests, and with which key, changes. So every harness the app
gives Claude (tools, hooks, permission prompts, `--resume`) runs unchanged. DeepSeek honours `output_config.effort`
at `low`, `high` and `max`. Without a level it thinks at `high`, so the app asks for `max` unless one is chosen.
The key is the hub's `.env` `DEEPSEEK_API_KEY`, else the server's environment (`common.settings.pick`).
"""

from __future__ import annotations

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


def environment(model: str | None, base: dict) -> dict:
    """`base` pointed at DeepSeek when `model` is its own, else `base` itself. A Claude login or key in `base`
    is dropped: either would win over the token and send the turn to Anthropic."""

    if model != MODEL:
        return base
    key = settings.pick(settings.entries(settings.HUB / ".env"), "DEEPSEEK_API_KEY")[0]
    if not key:
        raise RuntimeError("DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
    kept = {k: v for k, v in base.items() if k not in {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}}
    return {**kept, "ANTHROPIC_BASE_URL": URL, "ANTHROPIC_AUTH_TOKEN": key, **dict.fromkeys(ROLES, MODEL),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
