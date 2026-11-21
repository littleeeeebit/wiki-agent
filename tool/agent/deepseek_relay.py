"""The loopback relay that alone holds the DeepSeek key (`deepseek.relay()` owns the one a process uses).

The CLI's endpoint is this relay and its key a token only the relay accepts. A request that carries the token, and no
other credential, goes on to DeepSeek with the real key and DeepSeek's own headers alone. Anything else ends here.
A policy that sends the CLI somewhere else sends the token, never the key, and a credential a policy adds never
reaches DeepSeek.

Ownership: one asyncio loop on one thread owns the listener, every caller's connection, each request's own connection
to DeepSeek and every socket under them; no other thread touches a socket. Each connection is a task. A caller that
hangs up cancels its task, which cancels the exchange with DeepSeek wherever it is: connecting, the TLS handshake,
sending, waiting for headers or streaming. `close()` aborts every transport, cancels every task, closes the listener,
stops the loop and removes the token folder.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import secrets
import shutil
import ssl
import tempfile
import threading
import urllib.parse

import h11

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
CONNECT = 30.0       # seconds to reach DeepSeek at all
JOIN = 10.0          # seconds `close` waits for the loop to wind down
LINGER = 5.0         # seconds a finished connection reads away what its caller still sends
LONGEST_EVENT = 8 << 20   # bytes in one server-sent event; past it the stream ends there
LONGEST_BODY = 32 << 20   # bytes in one JSON answer; past it the caller gets the relay's error instead
BOM = b"\xef\xbb\xbf"


def nominated(headers: list[tuple[str, str]]) -> set[str]:
    """The header names a `Connection` field marks as this connection's own: never passed across."""

    return {n.strip().lower() for k, v in headers if k == "connection" for n in v.split(",") if n.strip()}


def _redact(value, key: str):
    if isinstance(value, str):
        return value.replace(key, "[key]")
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    return value


def redacted_json(raw: bytes, key: str) -> bytes | None:
    """`raw` as JSON with the key gone from every decoded string, so no escaped form survives; None when `raw` is not
    JSON or carries no key once decoded."""

    try:
        value = json.loads(raw)
    except ValueError:
        return None
    clean = _redact(value, key)
    return None if clean == value else json.dumps(clean, ensure_ascii=False).encode("utf-8")


class Events:
    """A server-sent event stream cut into whole events, each with the key gone: literal anywhere, JSON-escaped in its
    data. Line endings (CRLF, CR or LF) become LF and a leading BOM goes first, so the fields read here are the
    fields any parser reads; a CR that ends one chunk waits to see whether an LF follows."""

    def __init__(self, key: str):
        self.key, self.pending, self.tail, self.first = key, b"", b"", True

    def feed(self, chunk: bytes) -> list[bytes]:
        data = self.tail + chunk
        self.tail = b"\r" if data.endswith(b"\r") else b""
        self.pending += data[:len(data) - len(self.tail)].replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return self._whole()

    def _whole(self) -> list[bytes]:
        if self.first and len(self.pending) >= len(BOM):
            self.pending, self.first = self.pending.removeprefix(BOM), False
        out = []
        while (end := self.pending.find(b"\n\n")) >= 0:
            out.append(clean_event(self.pending[:end], self.key) + b"\n\n")
            self.pending = self.pending[end + 2:]
        if len(self.pending) > LONGEST_EVENT:
            raise ValueError("an event past LONGEST_EVENT")
        return out

    def rest(self) -> bytes:
        """At the end of the stream: a CR still held is a line end, and an unfinished event goes out cleaned."""

        self.pending += b"\n" if self.tail else b""
        self.tail = b""
        out = self._whole()
        rest, self.pending = self.pending.removeprefix(BOM) if self.first else self.pending, b""
        return b"".join(out) + (clean_event(rest, self.key) if rest else b"")


def clean_event(event: bytes, key: str) -> bytes:
    """One LF-framed event (without its blank line) with the key gone, literal or JSON-escaped in its data."""

    event = event.replace(key.encode(), b"[key]")
    lines = event.split(b"\n")
    field = [line == b"data" or line.startswith(b"data:") for line in lines]
    data = [line[5:].removeprefix(b" ") for line, is_data in zip(lines, field) if is_data]
    fixed = redacted_json(b"\n".join(data), key) if data else None
    if fixed is None:
        return event
    return b"\n".join([line for line, is_data in zip(lines, field) if not is_data] + [b"data: " + fixed])


def clean_body(body: bytes, key: str) -> bytes:
    body = body.replace(key.encode(), b"[key]")
    return redacted_json(body, key) or body


def error_kind(body: bytes, key: str) -> str:
    """The error's type and message from DeepSeek's JSON, decoded and redacted before it is cut short; `?` when it is
    not that shape."""

    try:
        error = json.loads(body.decode("utf-8")).get("error") or {}
    except (ValueError, AttributeError):
        return "?"
    if not isinstance(error, dict):
        return "?"
    return f"{error.get('type') or '?'} — {error.get('message') or ''}".replace(key, "[key]")[:300]


class Relay:
    def __init__(self, upstream: str, env: Path):
        # No pool, no cookie jar: each request opens its own connection, so nothing one turn leaves reaches the next.
        parts = urllib.parse.urlsplit(upstream)
        self.tls = ssl.create_default_context() if parts.scheme == "https" else None
        self.host, self.netloc, self.prefix = parts.hostname, parts.netloc, parts.path.rstrip("/")
        self.upstream_port = parts.port or (443 if self.tls else 80)
        self.env = env
        self.token = secrets.token_urlsafe(32)
        self.tasks: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()   # until their transport is gone, not just their task
        self.reapers: set[asyncio.Task] = set()
        self.folder: Path | None = None
        self.loop = asyncio.new_event_loop()
        ready, failed = threading.Event(), []

        def run():
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._start())
            except BaseException as exc:   # handed to the constructor, which raises it
                failed.append(exc)
                return
            finally:
                ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, name="deepseek-relay", daemon=True)
        self.thread.start()
        ready.wait()
        if failed:
            self.thread.join()
            self.loop.close()
            raise failed[0]
        try:   # a construction that fails half way leaves neither the listener nor the token behind
            self.folder = Path(tempfile.mkdtemp(prefix="deepseek-relay-"))
            (self.folder / ".env").write_text(f"DEEPSEEK_API_KEY={self.token}\n", encoding="utf-8")
        except BaseException:
            self.close()
            raise

    async def _start(self) -> None:
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def admitted(self, headers: list[tuple[str, str]]) -> bool:
        """The request carries the token, at most once in each credential header, and no other credential."""

        given = {name: [v for k, v in headers if k == name]
                 for name in ("x-api-key", "authorization", "proxy-authorization")}
        sent = given["x-api-key"] + given["authorization"]
        return (bool(sent) and not given["proxy-authorization"] and all(len(v) <= 1 for v in given.values())
                and all(v in (self.token, f"Bearer {self.token}") for v in sent))

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.tasks) >= WORKERS:   # full: closed before a byte is read
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.TimeoutError, h11.ProtocolError):
            pass   # the caller went silent past `IDLE`, broke off mid-message, or went away
        finally:
            self.tasks.discard(task)
            reaper = asyncio.ensure_future(self._reap(reader, writer))
            self.reapers.add(reaper)
            reaper.add_done_callback(self.reapers.discard)

    async def _reap(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A lingering close: the answer is sent and the write side shut, then what the caller still sends (a body
        refused unread) is read away for `LINGER`, since closing with it unread resets the connection on Windows and
        the caller loses the answer. A caller that never reads is aborted after `IDLE`."""

        try:
            if writer.can_write_eof():
                writer.write_eof()
            await asyncio.wait_for(hangup(reader), LINGER)
        except Exception:   # noqa: BLE001 - gone, aborted by `close`, or still sending: it closes all the same
            pass
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), IDLE)
        except Exception:   # noqa: BLE001 - the same: nothing is left open past this
            writer.transport.abort()
        finally:
            self.writers.discard(writer)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = h11.Connection(h11.SERVER)

        async def next_event():
            while (event := conn.next_event()) is h11.NEED_DATA:
                conn.receive_data(await reader.read(65536))
            return event

        try:
            request = await asyncio.wait_for(next_event(), IDLE)
        except h11.RemoteProtocolError:   # two different lengths, a broken chunk header, and the like
            return await answer(None, writer, 400, "DeepSeek 중계: 요청 형식이 틀렸다")
        if not isinstance(request, h11.Request):
            return None
        headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in request.headers]
        path, method = request.target.decode("latin-1"), request.method.decode("latin-1")

        def fail(status: int, message: str):   # a HEAD's answer carries no body, or h11 refuses to send it
            return answer(conn, writer, status, message, head=method == "HEAD")

        if not self.admitted(headers):   # before any body is read
            return await fail(401, "DeepSeek 중계: 이 앱이 준 토큰이 아니다")
        if method not in ("GET", "POST"):
            return await fail(405, f"DeepSeek 중계가 넘기지 않는 방식: {method}")
        if not path.startswith("/v1/"):
            return await fail(404, f"DeepSeek 중계가 넘기지 않는 경로: {path}")
        declared = [v for k, v in headers if k == "content-length"]
        # h11 refuses lengths that disagree and merges equal ones (RFC 9110 section 8.6); chunked never comes in.
        if any(k == "transfer-encoding" for k, _ in headers):
            return await fail(400, "DeepSeek 중계: 본문을 나눠 보내지 마라")
        if method == "GET" and declared and int(declared[0]) != 0:
            return await fail(400, "DeepSeek 중계: GET 에는 본문이 없다")
        if method == "POST" and not declared:
            return await fail(411, "DeepSeek 중계: 본문 길이가 없다")
        if declared and int(declared[0]) > LARGEST:   # h11 has already refused a length that is not a number
            return await fail(413, "DeepSeek 중계: 요청이 너무 크다")
        body = bytearray()
        # A caller that hangs up before its body is whole makes h11 raise: nothing half sent goes on.
        while not isinstance(event := await asyncio.wait_for(next_event(), IDLE), h11.EndOfMessage):
            body += event.data
        key = settings.entries(self.env).get("DEEPSEEK_API_KEY")
        if not key:
            return await fail(503, "DeepSeek 키가 없다: 허브의 .env 에 DEEPSEEK_API_KEY 를 넣어라")
        work = asyncio.ensure_future(self._forward(conn, writer, method, path, headers, bytes(body), key))
        gone = asyncio.ensure_future(hangup(reader))
        try:
            await asyncio.wait({work, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:   # the caller gone: the exchange with DeepSeek is cancelled wherever it stands
            for task in (work, gone):
                task.cancel()
            await asyncio.gather(work, gone, return_exceptions=True)

    async def _forward(self, conn, writer, method: str, path: str, headers, body: bytes, key: str) -> None:
        """One fresh connection to DeepSeek for this request, owned by this task: cancelling the task anywhere
        (connecting, the TLS handshake, sending, waiting, streaming) closes it, since asyncio closes a transport
        whose setup is cancelled and the `finally` aborts it after."""

        dropped = nominated(headers)
        sent = [(k, v) for k, v in headers if k in FORWARD and k not in dropped]
        sent += [("host", self.netloc), ("x-api-key", key), ("authorization", f"Bearer {key}"),
                 ("accept-encoding", "identity"), ("connection", "close")]
        if method == "POST":
            sent.append(("content-length", str(len(body))))
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.upstream_port, ssl=self.tls), CONNECT)
        except (OSError, asyncio.TimeoutError) as exc:
            return await answer(conn, writer, 502, f"DeepSeek 에 닿지 못했다 — {type(exc).__name__}")
        up = h11.Connection(h11.CLIENT)

        async def next_up():
            while (event := up.next_event()) is h11.NEED_DATA:
                up.receive_data(await asyncio.wait_for(up_reader.read(65536), UPSTREAM))
            return event

        try:
            up_writer.write(up.send(h11.Request(method=method, target=self.prefix + path, headers=sent))
                            + (up.send(h11.Data(data=body)) if body else b"") + up.send(h11.EndOfMessage()))
            await up_writer.drain()
            while isinstance(reply := await next_up(), h11.InformationalResponse):
                pass
            if not isinstance(reply, h11.Response):
                raise ConnectionError("DeepSeek closed before it answered")
            await self._stream(conn, writer, reply, next_up, key)
        except (OSError, asyncio.TimeoutError, h11.ProtocolError) as exc:
            if conn.our_state is h11.SEND_RESPONSE:
                await answer(conn, writer, 502, f"DeepSeek 에 닿지 못했다 — {type(exc).__name__}")
            # once the headers have gone out, the caller sees a stream that ends early: nothing more is written
        finally:
            up_writer.transport.abort()

    async def _stream(self, conn, writer, reply: h11.Response, next_up, key: str) -> None:
        async def chunks():
            while isinstance(event := await next_up(), h11.Data):
                yield bytes(event.data)

        back = [(k.decode("latin-1").lower(), v.decode("latin-1").replace(key, "[key]")) for k, v in reply.headers]
        dropped = nominated(back)
        kept = [(k, v) for k, v in back if k not in dropped and (k in BACK or k.startswith(BACK_PREFIX))]
        if 300 <= reply.status_code < 400:   # handed on, a client that follows it would leave the relay
            return await answer(conn, writer, 502, "DeepSeek 이 요청을 다른 곳으로 돌렸다 — 따라가지 않는다")
        if reply.status_code >= 400:   # the relay's own words: whatever upstream or a proxy wrote stays here
            said = bytearray()
            async for chunk in chunks():
                said += chunk
                if len(said) >= 1 << 20:
                    break
            return await answer(conn, writer, reply.status_code,
                                f"DeepSeek 오류 {reply.status_code}: {error_kind(bytes(said), key)}", kept)
        start = h11.Response(status_code=reply.status_code, headers=kept + [("connection", "close")])
        if not dict(back).get("content-type", "").startswith("text/event-stream"):
            body = bytearray()   # one JSON answer, cleaned whole before a byte of it goes out
            async for chunk in chunks():
                body += chunk
                if len(body) > LONGEST_BODY:
                    return await answer(conn, writer, 502, "DeepSeek 의 답이 너무 크다 — 넘기지 않는다")
            writer.write(conn.send(start) + conn.send(h11.Data(data=clean_body(bytes(body), key)))
                         + conn.send(h11.EndOfMessage()))
            return await writer.drain()
        writer.write(conn.send(start))
        await writer.drain()
        events = Events(key)
        async for chunk in chunks():
            try:
                ready = events.feed(chunk)   # a whole event goes on the moment it is complete
            except ValueError:
                return None   # past `LONGEST_EVENT`: the stream ends there, unfinished, which the caller can tell
            if ready:
                writer.write(conn.send(h11.Data(data=b"".join(ready))))
                await writer.drain()
        if rest := events.rest():
            writer.write(conn.send(h11.Data(data=rest)))
        writer.write(conn.send(h11.EndOfMessage()))
        await writer.drain()

    async def _stop(self) -> None:
        self.server.close()
        for writer in list(self.writers):   # a caller that stopped reading holds nothing open past this
            writer.transport.abort()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for writer in list(self.writers):   # those the cancelled tasks just handed to `_reap`
            writer.transport.abort()
        await asyncio.gather(*self.reapers, return_exceptions=True)
        await self.server.wait_closed()

    def close(self) -> None:
        if self.thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(self._stop(), self.loop).result(JOIN)
            except Exception:   # noqa: BLE001 - the loop stops and the folder goes regardless
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(JOIN)
        if not self.thread.is_alive() and not self.loop.is_closed():
            self.loop.close()
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)


async def hangup(reader: asyncio.StreamReader) -> None:
    """Returns when the caller hangs up (the person pressed Stop); whatever it sends after its request is ignored."""

    while await reader.read(65536):
        pass


async def answer(conn: h11.Connection | None, writer: asyncio.StreamWriter, status: int, message: str,
                 kept=(), head: bool = False) -> None:
    """The relay's own JSON error, then the connection closes. `conn` is None once h11 has refused the request; a
    HEAD gets the status and headers alone."""

    payload = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}},
                         ensure_ascii=False).encode("utf-8")
    headers = [(k, v) for k, v in kept if k != "content-type"]
    headers += [("content-type", "application/json"), ("content-length", str(len(payload))), ("connection", "close")]
    try:
        if conn is None:
            head = f"HTTP/1.1 {status} Error\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
            writer.write(head.encode("latin-1") + payload)
        else:
            sent = conn.send(h11.Response(status_code=status, headers=headers))
            writer.write(sent + (b"" if head else conn.send(h11.Data(data=payload))) + conn.send(h11.EndOfMessage()))
        await writer.drain()
    except OSError:
        pass
