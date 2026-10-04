"""A paired browser companion. Execution and records stay on the desktop."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import anyio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.process import background_options

from . import channels, query

HOST = "mobile.wiki-agent.invalid"
COOKIE = "__Host-wiki-mobile"
LOCAL = {"127.0.0.1", "localhost"}
LIFETIME = 30 * 24 * 60 * 60
APK = channels.WIKI / "artifacts" / "wiki-agent.apk"
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
        self.process: subprocess.Popen | None = None
        self.starting = False
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
                    "origin": self.origin, "error": self.error, "apk_available": APK.is_file()}

    def wait_ready(self, process, origin):
        # Printing an address precedes DNS propagation and connector readiness.
        # Offer pairing only after the public path actually reaches this server.
        while True:
            with self.lock:
                if self.process is not process or self.origin != origin or not self.starting:
                    return
            try:
                with urlopen(origin + "/api/mobile/status", timeout=3) as response:
                    reached = json.loads(response.read(1024)) == {"local": False, "paired": False}
                if reached:
                    with self.lock:
                        if self.process is process and self.origin == origin:
                            self.starting = False
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)

    def start(self) -> dict:
        with self.lock:
            if self.origin or self.starting:
                return self.status()
            # ponytail: temporary URL changes after a restart; --mobile-origin
            # with a named tunnel is the upgrade when a stable address matters.
            binary = shutil.which("cloudflared")
            if not binary:
                raise HTTPException(503, "cloudflared를 설치한 뒤 다시 연결하세요")
            self.key()
            self.error = ""
            try:
                process = subprocess.Popen(
                    [binary, "--no-autoupdate", "tunnel", "--url", f"http://127.0.0.1:{self.port}",
                     "--http-host-header", HOST], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **background_options())
            except OSError as exc:
                raise HTTPException(503, "외부 연결을 시작하지 못했습니다") from exc
            self.process, self.starting = process, True

        def read():
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                    with self.lock:
                        if self.process is not process:
                            return
                        if match:
                            self.origin = match[0]
                            threading.Thread(target=self.wait_ready, args=(process, match[0]), daemon=True).start()
                with self.lock:
                    if self.process is process:
                        self.origin, self.starting = "", False
                        self.error = "외부 연결이 끊겼습니다. 다시 연결하세요"
            finally:
                process.stdout.close()

        threading.Thread(target=read, daemon=True).start()

        def timeout():
            with self.lock:
                if self.process is process and self.starting:
                    self.stop()
                    self.error = "외부 연결에 시간이 너무 걸립니다. 인터넷 연결과 cloudflared 설정을 확인하세요"

        timer = threading.Timer(60, timeout)
        timer.daemon = True
        timer.start()
        return self.status()

    def stop(self):
        with self.lock:
            process, self.process = self.process, None
            self.origin, self.code, self.starting = "", "", False
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
