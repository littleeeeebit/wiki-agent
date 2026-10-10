"""Workflow ownership is exclusive across processes and released after a crash."""

from pathlib import Path
import asyncio
import http.client
import socket
import subprocess
import sys
import threading

import pytest
import uvicorn

from main.runtime import server_owner
from main import app, loop, runtime, specs


def test_a_killed_server_releases_ownership_without_removing_the_lock_file(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", "-c",
         "from pathlib import Path\nfrom main.runtime import server_owner\nimport sys\n"
         "with server_owner(Path(sys.argv[1])):\n print('owned', flush=True)\n sys.stdin.read()\n",
         str(tmp_path)], cwd=Path(__file__).parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            with server_owner(tmp_path):
                pytest.fail("A second process took the running server's workflows")
    finally:
        child.kill()
        child.communicate(timeout=10)
    assert (tmp_path / "server.lock").is_file()
    with server_owner(tmp_path):
        pass


def test_shutdown_keeps_server_ownership_until_its_review_driver_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "stopping", threading.Event())
    entered, requested, stopping, release, finished = (threading.Event() for _ in range(5))
    errors = []
    monkeypatch.setattr(specs, "SPECS", tmp_path / "raw/specs")
    monkeypatch.setattr(specs, "load", lambda *_: {"state": "리뷰 R1"})
    monkeypatch.setattr(loop, "recover", lambda: [])
    monkeypatch.setattr(loop, "_loops", {})
    monkeypatch.setattr(loop, "_cells", {})
    monkeypatch.setattr(loop, "_review_runs", {})
    monkeypatch.setattr(loop, "_seated", 0)
    monkeypatch.setattr(loop, "poll", lambda halt: halt.wait())
    for module in (app.planning, app.survey):
        monkeypatch.setattr(module, "recover", lambda: None)
    for module in (app.planning, app.query, app.work):
        monkeypatch.setattr(module, "close_all", lambda: None)
    monkeypatch.setattr(app.mobile.companion, "stop", lambda: None)

    def held_step(_loop):
        entered.set()
        assert release.wait(120), "The held driver was not released"
        return False

    monkeypatch.setattr(loop, "step", held_step)
    driver = loop.Loop("fixture", "review")
    original_stop = driver.stop

    def stop():
        original_stop()
        stopping.set()

    monkeypatch.setattr(driver, "stop", stop)

    async def server():
        async with app.lifespan(app.app):
            loop._loops[(driver.repo, driver.sid)] = driver
            driver.thread = threading.Thread(target=loop.drive, args=(driver,), daemon=True)
            driver.thread.start()
            assert requested.wait(10)

    def own():
        try:
            asyncio.run(server())
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    owner = threading.Thread(target=own, daemon=True)
    owner.start()
    try:
        assert entered.wait(120)
        requested.set()
        assert stopping.wait(120)
        assert not finished.wait(.2), "Server ownership was released while a review driver was alive"
        assert driver.thread.is_alive()
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            with server_owner(specs.SPECS.parent):
                pytest.fail("A replacement server acquired ownership before the old driver stopped")
    finally:
        requested.set()
        release.set()
        owner.join(10)
        if driver.thread:
            driver.thread.join(10)
    assert not owner.is_alive() and not driver.thread.is_alive() and not errors
    with server_owner(specs.SPECS.parent):
        pass


def test_a_shutdown_ends_running_turns_before_it_waits_for_their_streams(monkeypatch):
    # Ctrl+C reaches no provider, which leads its own group; an unbounded drain keeps a merge request owned.
    ended = threading.Event()
    monkeypatch.setattr(app, "close_turns", ended.set)

    async def turn_stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while not ended.is_set():
            await send({"type": "http.response.body", "body": b"data: running\n\n", "more_body": True})
            await asyncio.sleep(0.05)
        await send({"type": "http.response.body", "body": b""})

    server, sock = uvicorn.Server(uvicorn.Config(turn_stream, lifespan="off", log_level="error")), socket.socket()
    sock.bind(("127.0.0.1", 0))
    app.ended_first(server)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        client = http.client.HTTPConnection(*sock.getsockname(), timeout=10)
        client.request("GET", "/")
        assert client.getresponse().readline().startswith(b"data:")
        server.should_exit = True
        thread.join(10)
        assert ended.is_set() and not thread.is_alive()
    finally:
        ended.set()
        thread.join(10)
        sock.close()
