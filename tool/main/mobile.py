"""A paired browser companion. Execution and records stay on the desktop."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import tarfile
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.error import URLError
from urllib.request import urlopen

import anyio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.process import background_options
from common import errorlog

from . import channels, mobile_transport, query

HOST = "mobile.wiki-agent.invalid"
COOKIE = "__Host-wiki-mobile"
LOCAL = {"127.0.0.1", "localhost"}
LIFETIME = 30 * 24 * 60 * 60
# Not under artifacts/: after-merge clears that scratch directory (scratch_dirs).
APK = channels.WIKI / "raw" / "android" / "wiki-agent.apk"
router = APIRouter(prefix="/api/mobile")


def local(request: Request | WebSocket) -> bool:
    # Cloudflared overwrites Host with HOST. Forwarded requests never acquire
    # the unauthenticated desktop's privileges, even with a forged local Host.
    try:
        return urlsplit("//" + request.headers.get("host", "")).hostname in LOCAL and not any(
            name in request.headers for name in ("cf-connecting-ip", "x-forwarded-for", "x-forwarded-host"))
    except ValueError:
        return False


class Companion:
    def __init__(self):
        self.port = 8787
        self.origin = ""
        self.error = ""
        self.progress = ""
        self.process: subprocess.Popen | None = None
        self.starting = False
        self.cancel = threading.Event()
        self.code = ""
        self.deadline = 0.0
        self.lock = threading.RLock()
        self.sockets: set[WebSocket] = set()

    def key(self) -> bytes:
        path = query.LOGS / "mobile-key"
        with self.lock:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as out:
                    out.write(secrets.token_hex(32))
                path.chmod(0o600)
            return bytes.fromhex(path.read_text(encoding="utf-8"))

    def authenticated(self, request: Request | WebSocket) -> bool:
        token = request.cookies.get(COOKIE, "")
        try:
            expires, nonce, signature = token.split(".")
            message = f"{expires}.{nonce}"
            return int(expires) > time.time() and hmac.compare_digest(
                signature, hmac.new(self.key(), message.encode(), hashlib.sha256).hexdigest())
        except (ValueError, OSError):
            return False

    def allowed(self, request: Request | WebSocket) -> bool:
        if local(request):
            return request.headers.get("origin") in (None, "http://" + request.headers["host"])
        return bool(self.origin) and request.headers.get("host") == HOST and request.headers.get("origin") in (
            None, self.origin)

    def status(self) -> dict:
        with self.lock:
            return {"local": True, "enabled": bool(self.origin) and not self.starting, "starting": self.starting,
                    "origin": self.origin, "error": self.error, "progress": self.progress, "apk_available": APK.is_file()}

    def wait_ready(self, process):
        # Printing an address precedes DNS propagation and connector readiness.
        # Offer pairing only after the public path actually reaches this server.
        deadline = time.monotonic() + 45
        recorded = set()
        while time.monotonic() < deadline:
            with self.lock:
                if self.process is not process or self.cancel.is_set() or not self.starting:
                    return False
                origin = self.origin
            if process.poll() is not None:
                return False
            if not origin:
                self.cancel.wait(0.2)
                continue
            try:
                try:
                    with urlopen(origin.rstrip("/") + "/api/mobile/status", timeout=3,
                                 context=mobile_transport.tls_context()) as response:
                        result = json.loads(response.read(1024))
                except URLError as exc:
                    if not isinstance(exc.reason, socket.gaierror):
                        raise
                    with self.lock:
                        if self.process is process:
                            self.progress = "공개 주소를 자동으로 확인하는 중…"
                    result = mobile_transport.public_status(origin)
                if result == {"local": False, "paired": False}:
                    with self.lock:
                        if self.process is process and self.origin == origin and not self.cancel.is_set():
                            self.starting = False
                            self.error, self.progress = "", ""
                            return True
                    return False
                raise ValueError("Public address did not reach the mobile status endpoint")
            except (OSError, ValueError) as exc:
                reason = getattr(exc, "reason", exc)
                message = "외부 주소에서 PC에 도달하지 못했습니다. 잠시 후 다시 연결해 주세요"
                if isinstance(reason, ssl.SSLError):
                    message = "외부 연결의 보안 인증서를 확인하지 못했습니다"
                elif isinstance(reason, socket.gaierror) or "DNS" in str(exc):
                    message = "공개 주소 조회에 실패했습니다. 자동 복구 후에도 주소를 확인하지 못했습니다"
                with self.lock:
                    if self.process is process:
                        self.error = message
                if type(reason) not in recorded:
                    errorlog.record("mobile.probe", exc)
                    recorded.add(type(reason))
            self.cancel.wait(1)
        return False

    def start(self) -> dict:
        with self.lock:
            if self.origin or self.starting:
                return self.status()
            self.key()
            self.error = ""
            self.progress = "연결 도구를 자동으로 준비하는 중…"
            self.starting = True
            self.cancel = cancel = threading.Event()
        threading.Thread(target=self.prepare, args=(cancel,), daemon=True).start()
        return self.status()

    def prepare(self, cancel):
        try:
            directory = channels.WIKI / "raw" / "mobile-runtime" / mobile_transport.VERSION
            binary = mobile_transport.cloudflared(directory)
            directory.mkdir(parents=True, exist_ok=True)
            config = directory / "config.yml"
            config.write_text("{}\n", encoding="utf-8", newline="\n")
            # Ignore unrelated named-tunnel settings on a team member's PC.
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("TUNNEL_")}
            for protocol in ("quic", "http2"):
                with self.lock:
                    if self.cancel is not cancel or cancel.is_set():
                        return
                    self.origin = ""
                    self.progress = "외부 연결을 여는 중…" if protocol == "quic" else "다른 연결 방식으로 자동 재시도하는 중…"
                    process = subprocess.Popen(
                        [str(binary), "--no-autoupdate", "tunnel", "--config", str(config), "--protocol", protocol,
                         "--url", f"http://127.0.0.1:{self.port}", "--http-host-header", HOST],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                        text=True, encoding="utf-8", errors="replace", env=env, **background_options())
                    self.process = process
                threading.Thread(target=self.read, args=(process,), daemon=True).start()
                if self.wait_ready(process):
                    return
                with self.lock:
                    if self.process is process:
                        self.process, self.origin = None, ""
                self.reap(process)
                if cancel.is_set():
                    return
            self.failed(cancel, self.error or "중계 서버에 연결하지 못했습니다. 잠시 후 다시 연결해 주세요")
        except (OSError, ValueError, RuntimeError, tarfile.TarError) as exc:
            errorlog.record("mobile.start", exc)
            message = str(exc) if isinstance(exc, RuntimeError) else "연결 도구를 준비하거나 실행하지 못했습니다. 잠시 후 다시 연결해 주세요"
            self.failed(cancel, message)

    def read(self, process):
        try:
            for line in process.stdout:
                with self.lock:
                    if self.process is not process:
                        return
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                    if match and not self.origin:
                        self.origin = match[0]
                    if " ERR " in line or "failed" in line.lower():
                        errorlog.record("mobile.tunnel", line.strip())
            with self.lock:
                if self.process is process and not self.starting:
                    self.origin = ""
                    self.error = "외부 연결이 끊겼습니다. 다시 연결하세요"
        finally:
            process.stdout.close()

    def failed(self, cancel, message):
        with self.lock:
            if self.cancel is cancel and not cancel.is_set():
                process, self.process = self.process, None
                self.starting, self.origin, self.progress = False, "", ""
                self.error = message
            else:
                return
        self.reap(process)

    def stop(self):
        with self.lock:
            self.cancel.set()
            process, self.process = self.process, None
            self.origin, self.code, self.progress, self.error, self.starting = "", "", "", "", False
        self.reap(process)

    @staticmethod
    def reap(process):
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


companion = Companion()


def desktop(request: Request):
    if not local(request):
        raise HTTPException(403, "휴대폰 연결은 PC에서 관리하세요")


@router.get("/status")
def status(request: Request):
    return companion.status() if local(request) else {"local": False, "paired": companion.authenticated(request)}


@router.post("/start")
def start(request: Request):
    desktop(request)
    return companion.start()


@router.post("/link")
def link(request: Request):
    desktop(request)
    with companion.lock:
        if not companion.origin or companion.starting:
            raise HTTPException(409, "외부 연결을 먼저 시작하세요")
        companion.code, companion.deadline = secrets.token_urlsafe(24), time.monotonic() + 300
        return {"link": f"{companion.origin}/#pair={companion.code}", "seconds": 300}


class Pair(BaseModel):
    code: str = Field(max_length=128)


@router.post("/pair")
def pair(request: Request, body: Pair):
    if local(request) or request.headers.get("origin") != companion.origin:
        raise HTTPException(403, "휴대폰의 연결 주소에서 여세요")
    with companion.lock:
        if not companion.code or time.monotonic() >= companion.deadline or not secrets.compare_digest(
                companion.code, body.code):
            raise HTTPException(401, "연결 링크가 만료되었거나 이미 사용됐습니다. PC에서 새로 만드세요")
        companion.code = ""
        message = f"{int(time.time()) + LIFETIME}.{secrets.token_hex(16)}"
        signature = hmac.new(companion.key(), message.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"paired": True})
    response.set_cookie(COOKIE, f"{message}.{signature}", max_age=LIFETIME, secure=True, httponly=True, samesite="strict")
    return response


@router.post("/stop")
async def stop(request: Request):
    desktop(request)
    await asyncio.to_thread(companion.stop)
    with companion.lock:
        path = query.LOGS / "mobile-key"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    for ws in list(companion.sockets):
        try:
            await ws.close(code=1008)
        except RuntimeError:
            pass
    return companion.status()


@router.websocket("/request")
async def request_stream(ws: WebSocket):
    """Carry the existing HTTP routes over WebSocket, including their guards.

    Quick tunnels buffer SSE; binary WebSocket frames preserve the same stream.
    Only project and body cross from the client. Host, Origin and cookies come
    from the authenticated handshake, never from a client-supplied header map.
    """
    if local(ws) or not companion.allowed(ws) or ws.headers.get("origin") != companion.origin \
            or not companion.authenticated(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    companion.sockets.add(ws)
    try:
        data = await asyncio.wait_for(ws.receive_text(), timeout=10)
        if len(data) > 100_000:
            await ws.close(code=1009)
            return
        message = json.loads(data)
        target = urlsplit(message["url"])
        method = message.get("method", "GET")
        if target.scheme or target.netloc or target.fragment or not target.path.startswith("/api/") \
                or unquote(target.path).startswith("/api/mobile/") or method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            await ws.close(code=1008)
            return
        body = message.get("body", "").encode("utf-8")
        project = message.get("project", "").encode("ascii")
        if len(project) > 4096:
            await ws.close(code=1009)
            return
        headers = [(k, v) for k, v in ws.scope["headers"] if k.lower() in (b"host", b"origin", b"cookie")]
        headers += [(b"content-type", b"application/json"), (b"x-project", project)]
        scope = {**ws.scope, "type": "http", "method": method, "scheme": "https", "http_version": "1.1",
                 "path": unquote(target.path), "raw_path": target.path.encode(),
                 "query_string": target.query.encode(), "headers": headers, "extensions": {}}
        # Starlette's streaming disconnect listener must wait for the browser
        # to disconnect, not mistake the end of the request body for a close.
        consumed = False
        disconnected = asyncio.Event()

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(event):
            if event["type"] == "http.response.start":
                await ws.send_json({"status": event["status"], "headers": [
                    [k.decode("latin-1"), v.decode("latin-1")] for k, v in event["headers"]]})
            elif event["type"] == "http.response.body" and event.get("body"):
                await ws.send_bytes(event["body"])

        async def watch():
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                disconnected.set()

        run = asyncio.create_task(ws.app(scope, receive, send))
        watcher = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((run, watcher), return_when=asyncio.FIRST_COMPLETED)
            if run in done:
                await run
                await ws.close(code=1000)
        finally:
            run.cancel()
            watcher.cancel()
            # An outer ASGI cancel scope must not interrupt the child drain.
            with anyio.CancelScope(shield=True):
                await asyncio.gather(run, watcher, return_exceptions=True)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, asyncio.TimeoutError):
        await ws.close(code=1008)
    finally:
        companion.sockets.discard(ws)
