"""The loopback relay that alone holds the DeepSeek key (`deepseek.relay()` owns the one a process uses).

The CLI's endpoint is this relay and its key a token only the relay accepts. A request that carries the token, and no
other credential, goes on to DeepSeek with the real key and DeepSeek's own headers alone. Anything else ends here.
A policy that sends the CLI somewhere else sends the token, never the key, and a credential a policy adds never
reaches DeepSeek.

Ownership: the relay owns its listener, every connection it has accepted, every connection it opens to DeepSeek, the
threads that serve them, and a scratch folder with the token for the CLI's key helper. A caller that hangs up cuts its
request to DeepSeek at once, waiting for headers or reading the answer. `close()` cuts everything, joins the threads
and removes the folder.
"""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import select
import shutil
import socket
import tempfile
import threading
import urllib.parse

from common import settings

# Request headers the relay passes on. Everything else (any credential, cookie or custom header a policy adds) stays.
FORWARD = {"content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent"}
# Response headers it passes back. The rest (connection fields, cookies, whatever an upstream or a proxy adds) stays.
BACK = {"content-type", "retry-after", "request-id", "x-request-id"}
BACK_PREFIX = ("anthropic-ratelimit-", "x-ratelimit-")
LARGEST = 64 << 20   # bytes in one request: a turn with several screenshots stays well inside
WORKERS = 16         # connections served at once; the app's turns each hold one
IDLE = 120.0         # seconds the relay waits on a silent caller before it lets the connection go
UPSTREAM = 600.0     # seconds DeepSeek may stay silent mid-answer
JOIN = 10.0          # seconds `close` waits for a thread it has already cut loose


def nominated(headers) -> set[str]:
    """The header names a `Connection` field marks as this connection's own: never passed across."""

    return {n.strip().lower() for v in headers.get_all("connection") or [] for n in v.split(",") if n.strip()}


class Scrub:
    """The key, wherever it falls in a stream, replaced before a byte reaches the CLI. It holds back the few bytes
    that could start an occurrence the next chunk completes."""

    def __init__(self, key: str):
        self.key, self.held = key.encode(), b""

    def __call__(self, chunk: bytes) -> bytes:
        joined = (self.held + chunk).replace(self.key, b"[key]")
        keep = len(self.key) - 1
        self.held, out = (joined[-keep:], joined[:-keep]) if keep and len(joined) > keep else (joined, b"")
        return out

    def rest(self) -> bytes:
        out, self.held = self.held, b""
        return out


class _Server(ThreadingHTTPServer):
    daemon_threads = True   # interpreter exit never waits on a worker; `Relay.close` cuts and joins them itself

    def __init__(self, relay: Relay, handler):
        self.relay = relay
        super().__init__(("127.0.0.1", 0), handler)

    def verify_request(self, request, client_address) -> bool:
        return self.relay.slots.acquire(blocking=False)   # no slot: the server closes it before reading a byte

    def process_request(self, request, client_address) -> None:
        self.relay.hold(request)
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        self.relay.threads.add(threading.current_thread())
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.relay.threads.discard(threading.current_thread())
            self.relay.let_go(request)
            self.relay.slots.release()

    def handle_error(self, request, client_address) -> None:
        pass   # a connection cut by `close` or by its caller ends quietly; the caller already has its answer or none


class Relay:
    def __init__(self, upstream: str, env: Path):
        parts = urllib.parse.urlsplit(upstream)
        self.connection = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        self.host, self.prefix, self.env = parts.netloc, parts.path.rstrip("/"), env
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(WORKERS)
        self.open: set = set()   # sockets in flight, both sides: `close` cuts them
        self.threads: set = set()
        self.lock = threading.Lock()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            timeout = IDLE

            def do_POST(self):
                relay.serve(self)

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = _Server(self, Handler)
        folder = None
        try:   # a construction that fails half way leaves neither the listener nor the token behind
            folder = Path(tempfile.mkdtemp(prefix="deepseek-relay-"))
            (folder / ".env").write_text(f"DEEPSEEK_API_KEY={self.token}\n", encoding="utf-8")
        except BaseException:
            self.server.server_close()
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        self.folder = folder
        threading.Thread(target=self.server.serve_forever, name="deepseek-relay", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def hold(self, sock) -> None:
        with self.lock:
            self.open.add(sock)

    def let_go(self, sock) -> None:
        with self.lock:
            self.open.discard(sock)

    def admitted(self, headers) -> bool:
        """The request carries the token, at most once in each credential header, and no other credential."""

        given = {name: headers.get_all(name) or [] for name in ("x-api-key", "authorization", "proxy-authorization")}
        sent = given["x-api-key"] + given["authorization"]
        return (bool(sent) and not given["proxy-authorization"] and all(len(v) <= 1 for v in given.values())
                and all(v in (self.token, f"Bearer {self.token}") for v in sent))

    def framed(self, request: BaseHTTPRequestHandler) -> int | None:
        """The body length a request declares, when it declares it once and plainly: a POST names one length, and
        nothing comes chunked; a GET carries none."""

        lengths = request.headers.get_all("content-length") or []
        if request.headers.get_all("transfer-encoding") or len(lengths) > 1:
            return None
        if request.command == "GET":
            return 0 if not lengths or lengths[0].strip() == "0" else None
        try:
            size = int(lengths[0]) if lengths else -1
        except ValueError:
            return None
        return size if size >= 0 else None

    def serve(self, request: BaseHTTPRequestHandler) -> None:
        if not self.admitted(request.headers):   # before any body is read
            return answer(request, 401, "DeepSeek 중계: 이 앱이 준 토큰이 아니다")
        if not request.path.startswith("/v1/"):
            return answer(request, 404, f"DeepSeek 중계가 넘기지 않는 경로: {request.path}")
        size = self.framed(request)
        if size is None:
            return answer(request, 400, "DeepSeek 중계: 본문 길이가 없거나 둘이거나, 나눠 보냈다")
        if size > LARGEST:
            return answer(request, 413, "DeepSeek 중계: 요청이 너무 크다")
        try:
            body = request.rfile.read(size)
        except OSError:   # the caller went silent past `IDLE`, or away
            return None
        if len(body) < size:   # it hung up part way: nothing half sent goes on
            return answer(request, 400, "DeepSeek 중계: 본문이 끝까지 오지 않았다")
        key = settings.entries(self.env).get("DEEPSEEK_API_KEY")
        if not key:
            return answer(request, 503, "DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
        self.forward(request, body, key)

    def forward(self, request: BaseHTTPRequestHandler, body: bytes, key: str) -> None:
        dropped = nominated(request.headers)
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD and k.lower() not in dropped}
        upstream = self.connection(self.host, timeout=UPSTREAM)
        started, gone = False, threading.Event()
        try:
            upstream.connect()   # owned before a byte is sent: a hang-up cuts it from here on
            self.hold(upstream.sock)
            watcher = threading.Thread(target=watch, args=(request.connection, upstream.sock, gone), daemon=True)
            watcher.start()
            try:
                upstream.request(request.command, self.prefix + request.path, body=body if body else None,
                                 headers={**headers, "x-api-key": key, "Authorization": f"Bearer {key}"})
                reply = upstream.getresponse()   # http.client never follows a redirect
                started = True
                self.stream(request, reply, key)
            finally:
                gone.set()
                watcher.join()
                self.let_go(upstream.sock)
        except (OSError, http.client.HTTPException, ValueError) as exc:   # unreachable, timed out, cut, or gone
            if not started:
                answer(request, 502, f"DeepSeek 에 닿지 못했다 — {type(exc).__name__}")
            # once the headers have gone out, the caller sees a stream that ends early: nothing more is written
        finally:
            upstream.close()

    def stream(self, request: BaseHTTPRequestHandler, reply, key: str) -> None:
        if 300 <= reply.status < 400:   # handed on, a client that follows it would leave the relay
            return answer(request, 502, "DeepSeek 이 요청을 다른 곳으로 돌렸다 — 따라가지 않는다")
        if reply.status >= 400:   # the relay's own words: whatever upstream or a proxy wrote stays here
            return answer(request, reply.status, f"DeepSeek 오류 {reply.status}: {error_kind(reply.read(1 << 20), key)}",
                          kept=passed(reply.msg, key))
        scrub = Scrub(key)   # an error inside a 200 answer, or anything else, never carries the key either
        request.send_response(reply.status)
        for k, v in passed(reply.msg, key):
            request.send_header(k, v)
        request.end_headers()
        while chunk := reply.read1(65536):   # a server-sent event goes on as it arrives
            request.wfile.write(scrub(chunk))
            request.wfile.flush()
        request.wfile.write(scrub.rest())

    def close(self) -> None:
        self.server.shutdown()
        with self.lock:
            cut = list(self.open)
            threads = list(self.threads)
        for sock in cut:
            _cut(sock)
        for thread in threads:
            thread.join(JOIN)
        self.server.server_close()
        shutil.rmtree(self.folder, ignore_errors=True)


def passed(msg, key: str) -> list[tuple[str, str]]:
    dropped = nominated(msg)
    return [(k, v.replace(key, "[key]")) for k, v in msg.items()
            if k.lower() not in dropped and (k.lower() in BACK or k.lower().startswith(BACK_PREFIX))]


def error_kind(body: bytes, key: str) -> str:
    """The error's type and message from DeepSeek's JSON, decoded first so no escaped form of the key survives the
    scrub; `?` when it is not that shape."""

    try:
        error = json.loads(body.decode("utf-8")).get("error") or {}
    except (ValueError, AttributeError):
        return "?"
    said = f"{error.get('type') or '?'} — {str(error.get('message') or '')[:300]}" if isinstance(error, dict) else "?"
    return said.replace(key, "[key]")


def answer(request: BaseHTTPRequestHandler, status: int, message: str, kept=()) -> None:
    payload = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}},
                         ensure_ascii=False).encode("utf-8")
    try:
        request.send_response(status)
        request.send_header("content-type", "application/json")
        for k, v in kept:
            if k.lower() != "content-type":
                request.send_header(k, v)
        request.end_headers()
        request.wfile.write(payload)
    except OSError:
        pass


def watch(client, upstream, done: threading.Event) -> None:
    """While a request is out, a caller that hangs up (the person pressed Stop) cuts it at DeepSeek too, whether it
    waits for headers or reads the answer."""

    while not done.wait(0.5):
        try:
            ready, _, _ = select.select([client], [], [], 0)
            if ready and client.recv(1, socket.MSG_PEEK) == b"":
                break
        except (OSError, ValueError):
            break
    else:
        return
    _cut(upstream)


def _cut(sock) -> None:
    """End a socket another thread may be blocked on. On Windows a shutdown alone does not wake a blocked read, so
    the descriptor is closed too, past the file objects (`makefile`) that would otherwise keep it open."""

    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock._real_close()
        except OSError:
            pass
