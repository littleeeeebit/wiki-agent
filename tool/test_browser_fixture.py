"""Fixture startup shares one bounded wait and closes only its own socket."""

import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

_spec = importlib.util.spec_from_file_location(
    "browser_fixture", Path(__file__).resolve().parents[1] / "web/tests/browser_fixture.py")
browser_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(browser_fixture)


def test_fixture_startup_preserves_success_and_cleans_timeout():
    sock, called = Mock(), threading.Event()
    server = SimpleNamespace(started=True, should_exit=False)

    def run(*, sockets):
        assert sockets == [sock]
        called.set()

    server.run = run
    thread = browser_fixture.start(server, sock)
    thread.join(5)
    assert called.is_set() and not thread.is_alive() and not server.should_exit
    sock.close.assert_not_called()

    called.clear()
    server.started = False
    with patch.object(browser_fixture.time, "sleep"):
        with pytest.raises(RuntimeError, match="Fixture server did not start"):
            browser_fixture.start(server, sock)
    assert called.is_set() and server.should_exit
    sock.close.assert_called_once_with()
