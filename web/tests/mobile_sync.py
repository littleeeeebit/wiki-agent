"""Two independent clients, real query records/feed and authenticated WSS.

python web/tests/mobile_sync.py
Uses isolated temporary records, a stub model and a loopback-only TLS relay.
No tunnel is published and no user's server is opened or restarted.
"""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

# Do not read the operator's provider credentials or send telemetry.
for name in ("TYPESAFE_API_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(name, None)
os.environ["WIKI_JEV_MODE"] = "off"

from mobile_browser import ROOT, fixture  # noqa: E402
from agent.chat_session import Event  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from main import app, knowledge, loop, mobile, query, work  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import uvicorn  # noqa: E402


def listener(application, **options):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(application, log_level="error", proxy_headers=False,
                                          ws="wsproto", timeout_graceful_shutdown=1, **options))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, sock
        time.sleep(.05)
    raise RuntimeError("Fixture server did not start")


def main():
    finished = threading.Event()
    sent = []
    feed_offline = threading.Event()

    class Model:
        def say(self, text, halt=None):
            # The real query path may prepend unseen task-result context.
            utterance = text.splitlines()[-1]
            sent.append(utterance)
            yield Event("delta", "Live " + utterance)
            if not finished.wait(15):
                raise RuntimeError("The test did not release its model")
            yield Event("done", "Answer " + utterance, {"session_id": "fixture", "error": False})

    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)
    for path, handler, method in [
        ("/api/log/{cid}", query.log, "GET"), ("/api/say/{cid}", query.say, "POST"),
        ("/api/reset/{cid}", query.reset, "POST"),
        ("/api/knowledge/runs/{run_id}/events", query.knowledge_events, "GET")]:
        fake.add_api_route(path, handler, methods=[method])

    @fake.get('/api/loops/events')
    def feed_events(after: int | None = None):
        if feed_offline.is_set():
            raise HTTPException(503, 'Synthetic feed outage')
        return loop.events(after)

    @fake.api_route("/api/{path:path}", methods=["GET", "POST"])
    async def api(path: str, request: Request):
        if path == "knowledge/status":
            return {"runs": knowledge.running(query.current_repo())}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web" / "dist", html=True))
    relay_loop = []

    # Mimic the relay's fixed Host rewrite, without mocking WebSocket frames,
    # application routes, cookies, middleware or stream producers.
    async def relay(scope, receive, send):
        if scope['type'] == 'websocket' and not relay_loop:
            relay_loop.append(asyncio.get_running_loop())
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "headers": [(key, mobile.HOST.encode() if key == b"host" else value)
                                           for key, value in scope["headers"]]}
        await fake(scope, receive, send)

    with tempfile.TemporaryDirectory(prefix="wiki-sync-") as scratch, ExitStack() as stack:
        root = Path(scratch)
        repo = root / "fixture"
        repo.mkdir()
        for owner, name, value in [
            (query, "LOGS", root / "logs"), (query, "current_repo", lambda: repo),
            (query, "project", lambda: "fixture"), (query, "_busy", {}), (query, "_sessions", {}),
            (query, "config", lambda *args: {"model": "fixture", "effort": ""}),
            (query, "session", lambda cid: Model()), (query, "hits_for", lambda text: []),
            (query, "explain", lambda text, *args: iter([Event("done", text)])),
            (knowledge, "runs_root", lambda: root / "runs"), (knowledge, "LIVE", {}),
            (work, "feed", work.Feed()), (mobile, "companion", mobile.Companion())]:
            stack.enter_context(patch.object(owner, name, value))
        stack.enter_context(patch.dict(os.environ, {"JEV_ENV": str(root / "absent.env"),
                                                    "TRANSLATE_ENV": str(root / "absent.env")}))
        openssl = shutil.which("openssl") or "C:/Program Files/Git/usr/bin/openssl.exe"
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=wiki-mobile.test", "-keyout", str(root / "key.pem"),
                        "-out", str(root / "cert.pem")], check=True, capture_output=True)
        servers = []
        try:
            servers.append(listener(fake))
            servers.append(listener(relay, ssl_keyfile=str(root / "key.pem"),
                                    ssl_certfile=str(root / "cert.pem")))
            desktop = f"http://127.0.0.1:{servers[0][2].getsockname()[1]}"
            origin = f"https://wiki-mobile.test:{servers[1][2].getsockname()[1]}"
            mobile.companion.origin = origin
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-proxy-server",
                    "--host-resolver-rules=MAP wiki-mobile.test 127.0.0.1"])
                pc = browser.new_page(viewport={"width": 1440, "height": 900})
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 844, "height": 390})
                phone = context.new_page()
                phone.clock.install()
                errors = []
                for page in (pc, phone):
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                pc.goto(desktop)
                pc.locator('.query-toolbar').get_by_role("button", name="위키", exact=True).wait_for()
                link = pc.request.post(desktop + "/api/mobile/link", headers={"X-Project": "fixture"}).json()["link"]
                phone.goto(link)
                phone.wait_for_function("document.documentElement.dataset.mobileClient === 'remote'")
                phone.evaluate("""() => {
                    document.documentElement.dataset.nativeShell = 'android';
                    document.documentElement.dataset.nativeMode = 'landscape';
                    window.dispatchEvent(new CustomEvent('mobile-screen-mode', {detail:'landscape'}));
                }""")
                phone.locator('.query-toolbar').get_by_role("button", name="위키", exact=True).wait_for()
                assert "pair=" not in phone.url
                cookie = next(c for c in context.cookies() if c['name'] == mobile.COOKIE)
                assert cookie['secure'] and cookie['httpOnly']

                def conversation(page):
                    return page.locator('.conversation-pane')

                def send(page, text):
                    conversation(page).get_by_role('textbox').fill(text)
                    conversation(page).get_by_role('button', name='보내', exact=True).click()

                def answer(text):
                    for page in (pc, phone):
                        conversation(page).get_by_text('Live ' + text, exact=True).wait_for(timeout=10000)
                    finished.set()
                    for page in (pc, phone):
                        conversation(page).get_by_text('Answer ' + text, exact=True).wait_for(timeout=10000)
                    for page in (pc, phone):
                        conversation(page).get_by_role('textbox').fill('Ready check')
                        page.wait_for_function("""() => !document.querySelector('.conversation-pane .composer button').disabled""")
                        conversation(page).get_by_role('textbox').fill('')

                send(pc, 'PC to phone')
                answer('PC to phone')
                finished.clear()
                send(phone, 'Phone to PC')
                answer('Phone to PC')
                print('PASS: PC → phone and phone → PC, including live partial answers over authenticated WSS')

                # A hidden focus updates its record, not the active focus.
                phone.locator('.query-toolbar').get_by_role('button', name='회고', exact=True).click()
                query.remember('wiki', 'result', 'Wiki-only result', repo=repo)
                conversation(pc).get_by_text('Wiki-only result', exact=True).wait_for()
                assert not conversation(phone).get_by_text('Wiki-only result', exact=True).count()
                phone.locator('.query-toolbar').get_by_role('button', name='위키', exact=True).click()
                conversation(phone).get_by_text('Wiki-only result', exact=True).wait_for()

                # Hold a feed closed while PC records change. Reconnect must
                # preserve the document/draft and recover without a POST retry.
                conversation(phone).get_by_role('textbox').fill('Unsent phone draft')
                phone.evaluate("window.__sameDocument = true")
                phone.clock.pause_at(time.time() * 1000 + 1000)
                feed_offline.set()
                async def disconnect():
                    for ws in list(mobile.companion.sockets):
                        await ws.close(code=1012)
                asyncio.run_coroutine_threadsafe(disconnect(), relay_loop[0]).result(timeout=5)
                phone.get_by_role('button', name='다시 연결', exact=True).wait_for()
                query.remember('wiki', 'result', 'Recorded while phone offline', repo=repo)
                feed_offline.clear()
                # Clock is paused to keep automatic retry separate from this
                # manual path; fire the real button handler without RAF waits.
                phone.get_by_role('button', name='다시 연결', exact=True).evaluate('(button) => button.click()')
                conversation(phone).get_by_text('Recorded while phone offline', exact=True).wait_for(timeout=10000)
                assert phone.evaluate('window.__sameDocument === true')
                assert conversation(phone).get_by_role('textbox').input_value() == 'Unsent phone draft'
                for page in (pc, phone):
                    for text in ('Answer PC to phone', 'Answer Phone to PC'):
                        assert conversation(page).get_by_text(text, exact=True).count() == 1
                assert sent == ['PC to phone', 'Phone to PC'], sent
                print('PASS: reconnect snapshot, no duplicate instructions/answers, retained draft and focus isolation')
                # Network-online recovery must also discover and stream an
                # answer started on PC while this client was disconnected.
                finished.clear()
                context.set_offline(True)
                asyncio.run_coroutine_threadsafe(disconnect(), relay_loop[0]).result(timeout=5)
                phone.get_by_role('button', name='다시 연결', exact=True).wait_for()
                send(pc, 'Resume live answer')
                conversation(pc).get_by_text('Live Resume live answer', exact=True).wait_for()
                context.set_offline(False)
                conversation(phone).get_by_text('Live Resume live answer', exact=True).wait_for(timeout=10000)
                finished.set()
                for page in (pc, phone):
                    conversation(page).get_by_text('Answer Resume live answer', exact=True).wait_for(timeout=10000)
                assert conversation(phone).get_by_role('textbox').input_value() == 'Unsent phone draft'
                assert sent == ['PC to phone', 'Phone to PC', 'Resume live answer'], sent
                print('PASS: network-online recovery attaches to the externally started active run and retains the draft')
                response = pc.request.post(desktop + '/api/reset/wiki', headers={'X-Project': 'fixture'},
                                           data={'keep': 'delete'})
                assert response.ok
                for page in (pc, phone):
                    conversation(page).get_by_text('Recorded while phone offline', exact=True).wait_for(state='detached')
                assert errors == [], errors
                print('PASS: clearing a conversation propagates to both clients')
                browser.close()
        finally:
            finished.set()
            with work.feed.wake:
                work.feed.done = True
                work.feed.wake.notify_all()
            for server, thread, sock in servers:
                server.should_exit = True
                thread.join(timeout=5)
                sock.close()


if __name__ == '__main__':
    main()
