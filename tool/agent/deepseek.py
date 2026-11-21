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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

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
# Request headers the relay passes on. Everything else (any credential, cookie or custom header a policy adds) stays.
FORWARD = {"content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent"}
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "set-cookie", "server", "date"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):   # a redirect would carry the key to where it points
        return None


class Relay:
    """A loopback relay, the only holder of the DeepSeek key. The CLI's endpoint is this relay and its key a token
    only this relay accepts. A request that carries the token in every credential header it has goes on to DeepSeek
    with the real key and the allowed headers alone. Anything else ends here. A policy that sends the CLI somewhere
    else sends the token, never the key, and a credential a policy adds never reaches DeepSeek.

    Ownership: one per process (`relay()`), started on first use, closed at exit (`close()`). It holds a loopback
    server on its own thread and a scratch folder with the token for the key helper."""

    def __init__(self, upstream: str = URL, env: Path | None = None):
        self.upstream, self.env = upstream.rstrip("/"), env or settings.HUB / ".env"
        self.token = secrets.token_urlsafe(32)
        self.folder = Path(tempfile.mkdtemp(prefix="deepseek-relay-"))
        (self.folder / ".env").write_text(f"DEEPSEEK_API_KEY={self.token}\n", encoding="utf-8")
        self.opener = urllib.request.build_opener(_NoRedirect)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                relay.serve(self)

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="deepseek-relay", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def admitted(self, headers) -> bool:
        """Every credential the request carries is the token, and it carries one."""

        given = [v for v in (headers.get("x-api-key"), headers.get("authorization")) if v is not None]
        return bool(given) and all(v in (self.token, f"Bearer {self.token}") for v in given) \
            and headers.get("proxy-authorization") is None

    def serve(self, request: BaseHTTPRequestHandler) -> None:
        body = request.rfile.read(int(request.headers.get("Content-Length") or 0))
        if not self.admitted(request.headers):
            return self.answer(request, 401, "DeepSeek 중계: 이 앱이 준 토큰이 아니다")
        if not request.path.startswith("/v1/"):
            return self.answer(request, 404, f"DeepSeek 중계가 넘기지 않는 경로: {request.path}")
        key = settings.entries(self.env).get("DEEPSEEK_API_KEY")
        if not key:
            return self.answer(request, 503, "DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD}
        outgoing = urllib.request.Request(self.upstream + request.path, data=body if request.command == "POST" else None,
                                          method=request.command,
                                          headers={**headers, "x-api-key": key, "Authorization": f"Bearer {key}"})
        try:
            with self.opener.open(outgoing, timeout=600) as up:
                self.stream(request, up.status, up.headers, up)
        except urllib.error.HTTPError as exc:
            with exc:
                self.stream(request, exc.code, exc.headers, exc)
        except (OSError, ValueError) as exc:   # unreachable, timed out, or the client went away
            try:
                self.answer(request, 502, f"DeepSeek 에 닿지 못했다 — {type(exc).__name__}")
            except OSError:
                pass

    @staticmethod
    def stream(request: BaseHTTPRequestHandler, status: int, headers, body) -> None:
        request.send_response(status)
        for k, v in headers.items():
            if k.lower() not in HOP:
                request.send_header(k, v)
        request.end_headers()
        read = getattr(body, "read1", body.read)
        while chunk := read(65536):   # a server-sent event goes on as it arrives
            request.wfile.write(chunk)
            request.wfile.flush()

    @staticmethod
    def answer(request: BaseHTTPRequestHandler, status: int, message: str) -> None:
        payload = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}},
                             ensure_ascii=False).encode("utf-8")
        request.send_response(status)
        request.send_header("content-type", "application/json")
        request.end_headers()
        request.wfile.write(payload)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.folder, ignore_errors=True)


_relay: Relay | None = None
_relay_lock = threading.Lock()


def relay() -> Relay:
    global _relay
    with _relay_lock:
        if _relay is None:
            _relay = Relay()
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
