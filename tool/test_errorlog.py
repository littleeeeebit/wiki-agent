"""Error diagnostics survive workflow failures without recording credentials."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from common import errorlog
from main import app, specs, work
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


def test_quoted_and_nested_secrets_are_redacted_before_json_encoding(monkeypatch):
    secret = 'escaped-fixture"password\\with\nnewline'
    monkeypatch.setenv("FIXTURE_PASSWORD", secret)
    errorlog.record("fixture", "failure", stack='password="fixture-password"',
                    fields={"API_KEY": "structured-fixture", "values": [secret, ("other", secret)]})
    rows = [json.loads(line) for line in errorlog.FILE.read_text(encoding="utf-8").splitlines()]
    row = next(row for row in rows if row["source"] == "fixture")
    assert row["stack"] == 'password="[redacted]"'
    assert row["fields"] == {"API_KEY": "[redacted]", "values": ["[redacted]", ["other", "[redacted]"]]}
    assert not any(secret in json.dumps(rows) for secret in ("fixture-password", "escaped-fixture", "structured-fixture"))


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


@pytest.mark.parametrize("assignment", [
    'password="fixture first second"',
    "secret='fixture,first second'",
    'api_key="fixture\\"first second"',
    'access_token="fixture\nfirst second"',
    'password="fixture first second',
    'password="fixture first second\\',
])
def test_quoted_secret_is_removed_in_full_including_truncated_values(assignment):
    errorlog.record("fixture", RuntimeError(assignment), stack=assignment)
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    key, value = assignment.split("=", 1)
    expected = f"{key}={value[0]}[redacted]{value[0]}"
    assert row["error"] == row["stack"] == expected
    assert "first" not in row["traceback"] and "second" not in row["traceback"]


@pytest.mark.parametrize("environment", [False, True])
def test_large_secrets_are_redacted_before_either_size_cap(monkeypatch, environment):
    secret = "fixture-tail-" * 2000
    if environment:
        monkeypatch.setenv("FIXTURE_SECRET", secret)
    message = secret if environment else f'password="{secret}"'
    errorlog.record("fixture", RuntimeError(message))
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert "fixture-tail-" not in row["error"] and "fixture-tail-" not in row["traceback"]
    assert "[redacted]" in row["error"] and "[redacted]" in row["traceback"]
    assert len(row["error"]) <= 8000 and len(row["traceback"]) <= 16000


def test_error_and_traceback_size_caps_still_bound_nonsecret_messages():
    errorlog.record("fixture", RuntimeError("x" * 26000))
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert len(row["error"]) == 8000 and len(row["traceback"]) == 16000


def test_caught_task_check_failure_keeps_the_exception_traceback(monkeypatch, tmp_path):
    def broken(*_args):
        raise RuntimeError("fixture task check failed")

    monkeypatch.setattr(specs, "_check", broken)
    run = work.Run(SimpleNamespace(id="fixture", repo="fixture", parent_id=None))
    assert specs.check(tmp_path, run, "Finished") is None
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert row["source"] == "task-check" and row["turn"] == run.turn
    assert "RuntimeError: fixture task check failed" in row["traceback"]


def test_broken_task_file_is_logged_while_missing_task_is_ordinary(tmp_path):
    assert specs.load("fixture", "absent") is None
    assert not errorlog.FILE.exists()
    file = specs.file_of("fixture", "broken")
    file.parent.mkdir(parents=True)
    file.write_text("{", encoding="utf-8")
    assert specs.load("fixture", "broken") is None
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert (row["source"], row["repo"], row["spec"]) == ("task-read", "fixture", "broken")
    assert row["type"] == "JSONDecodeError"


@pytest.mark.parametrize("failure", ["open", "emit", "rotation"])
def test_logging_failure_emits_a_redacted_fallback_and_can_retry(monkeypatch, capsys, failure):
    from logging.handlers import RotatingFileHandler

    if failure != "open":
        errorlog.record("fixture", "initial")
    with monkeypatch.context() as broken:
        if failure == "open":
            broken.setattr(RotatingFileHandler, "_open", lambda _: (_ for _ in ()).throw(OSError("disk unavailable")))
        elif failure == "emit":
            broken.setattr(errorlog._logger.handlers[0], "emit", lambda _: (_ for _ in ()).throw(OSError("disk unavailable")))
        else:
            handler = errorlog._logger.handlers[0]
            handler.maxBytes = 1
            broken.setattr(handler, "doRollover", lambda: (_ for _ in ()).throw(OSError("disk unavailable")))
        errorlog.record("fixture", RuntimeError('password="fixture secret with spaces"'))
    fallback = capsys.readouterr().err
    assert '"source": "fixture"' in fallback and "[redacted]" in fallback
    assert "fixture secret" not in fallback
    errorlog.record("fixture", "Recovered logging")
    row = json.loads(errorlog.FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert row["error"] == "Recovered logging"
