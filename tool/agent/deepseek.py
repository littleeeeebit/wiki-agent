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

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

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
         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
         "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD")
# A shell runs the helper: these would expand or split inside its double quotes.
SHELL = re.compile(r'[%!$`"^&|<>]')
PROBE_KEY = "deepseek-route-probe"
VERIFIED_FOR = 600.0   # seconds a passed route check stands: policy rarely changes under a running server
_verified = {"until": 0.0}
_verify_lock = threading.Lock()


def built(given: dict, url: str, env: Path) -> dict:
    helper = Path(__file__).resolve().parents[1] / "deepseek_key.py"
    paths = (sys.executable, str(helper), str(env))
    if any(SHELL.search(p) for p in paths):
        raise RuntimeError(f"DeepSeek 키 도우미 경로에 셸 문자가 있다: {' · '.join(paths)}")
    return {**given, "apiKeyHelper": " ".join(f'"{p}"' for p in paths),
            "env": {"ANTHROPIC_BASE_URL": url, **dict.fromkeys(ROLES, MODEL),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **dict.fromkeys(ROUTE, "")}}


def verify_route(given: dict) -> None:
    """Start the CLI once with these very settings, pointed at a loopback stand-in and a dummy key, and refuse unless
    the request reaches the stand-in carrying that key alone. Managed settings, an MDM profile, a policy helper or a
    signed-in gateway all outrank `--settings`; whichever of them reroutes the CLI, its effect shows here, at no
    cost to the real key."""

    from .chat_local import cli_command

    with _verify_lock:
        if time.monotonic() < _verified["until"]:
            return
        seen: list[tuple] = []

        class Stand(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if "messages" in self.path:
                    seen.append((self.headers.get("Authorization"), self.headers.get("x-api-key")))
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"type":"error","error":{"type":"invalid_request_error","message":"route probe"}}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Stand)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="deepseek-route-") as scratch:
                (Path(scratch) / ".env").write_text(f"DEEPSEEK_API_KEY={PROBE_KEY}\n", encoding="utf-8")
                probe = built({**given, "disableAllHooks": True}, f"http://127.0.0.1:{server.server_port}",
                              Path(scratch) / ".env")
                try:
                    subprocess.run([*cli_command("claude"), "-p", "route probe", "--model", MODEL, "--settings",
                                    json.dumps(probe), "--strict-mcp-config", "--no-session-persistence", "--tools", ""],
                                   cwd=scratch, env=environment(MODEL, dict(os.environ)), stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=90)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            server.shutdown()
            server.server_close()
        honest = {f"Bearer {PROBE_KEY}", PROBE_KEY, None}
        if not seen or any(auth not in honest or key not in honest for auth, key in seen):
            raise RuntimeError("이 PC 의 Claude CLI 는 DeepSeek 경로를 따르지 않는다 — 관리 정책·MDM·로그인한 게이트웨이가 "
                               "엔드포인트나 인증을 정한다. DeepSeek 은 쓰지 않는다")
        _verified["until"] = time.monotonic() + VERIFIED_FOR


def flags(model: str | None, given: dict) -> dict:
    """The `--settings` JSON: `given` itself, or for DeepSeek with its endpoint, key helper and model names, once
    the CLI is seen to follow them (`verify_route`)."""

    if model != MODEL:
        return given
    env = settings.HUB / ".env"
    if not settings.entries(env).get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
    made = built(given, URL, env)
    verify_route(given)
    return made


def environment(model: str | None, base: dict) -> dict:
    """`base` without a credential for DeepSeek's turns, else `base` itself. A Claude login or key would beat the
    key helper and send the turn to Anthropic; the DeepSeek key comes from the helper alone."""

    if model != MODEL:
        return base
    dropped = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "DEEPSEEK_API_KEY"}
    return {k: v for k, v in base.items() if k not in dropped}
