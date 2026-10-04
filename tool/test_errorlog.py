"""Error diagnostics survive workflow failures without recording credentials."""

import asyncio
import json
from types import SimpleNamespace

from starlette.requests import Request

from common import errorlog
from main import app, work
from test_main import client, no_machine_settings  # noqa: F401


def test_http_screen_and_background_errors_share_utf8_redacted_records(monkeypatch):
    monkeypatch.setenv("FIXTURE_API_KEY", "private-fixture-secret")
    web = client()
    assert web.get("/api/unknown").status_code == 404
    assert web.post("/api/errors", json={"message": "실패 Bearer private-fixture-secret", "stack": "fixture"}).status_code == 200
    run = work.Run(SimpleNamespace(id="fixture", repo="fixture"))
    run.put({"kind": "error", "text": "failed: sk-fixturetoken"})

    async def broken(_request):
        raise RuntimeError("fixture exception")

    try:
        asyncio.run(app.log_failures(Request({"type": "http", "method": "GET", "path": "/api/fixture",
                                             "headers": [], "query_string": b"secret=private-fixture-secret"}), broken))
    except RuntimeError:
        pass
    content = errorlog.FILE.read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines()]
    assert {"http", "screen", "agent"} <= {row["source"] for row in rows}
    assert "private-fixture-secret" not in content.decode("utf-8") and "sk-fixturetoken" not in content.decode("utf-8")
    assert any("RuntimeError" in row.get("traceback", "") for row in rows)


def test_rotation_and_logging_failure_do_not_break_the_caller(monkeypatch):
    errorlog.record("fixture", "initial")
    handler = errorlog._logger.handlers[0]
    handler.maxBytes = 250
    for _ in range(10):
        errorlog.record("fixture", "message" * 20)
    assert len(list(errorlog.FILE.parent.glob("errors.jsonl*"))) <= 4
    assert errorlog.FILE.with_name("errors.jsonl.1").exists()
    monkeypatch.setattr(handler, "emit", lambda _record: (_ for _ in ()).throw(OSError("Disk unavailable")))
    errorlog.record("fixture", "must not raise")
