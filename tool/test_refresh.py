"""A value every request reads but none waits for (`main.refresh`), and the Codex model list that uses it.

Every computation here is a stand-in held open by an `Event`, so each case can
change things while one runs; nothing starts Codex.
"""

import threading
import time
from unittest.mock import patch

import pytest

from main import channels, refresh, runtime


def until(check, what: str) -> None:
    for _ in range(200):
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(what)


class Held:
    """A computation that returns the next of `answers` once `go` is set, counting its starts."""

    def __init__(self, *answers):
        self.go, self.answers, self.starts, self.ended = threading.Event(), list(answers), 0, threading.Event()

    def __call__(self):
        self.starts += 1
        self.go.wait(10)
        answer = self.answers.pop(0)
        self.ended.set()
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_read_never_waits_and_says_a_computation_runs_even_over_an_old_value():
    work, done = Held("old", "new"), threading.Event()
    value = refresh.Refresh("t", work, ttl=0.2, retry=60, done=done.set)
    started = time.monotonic()
    assert value.read() == (None, "", True) and time.monotonic() - started < 1, "기다리지 않는다"
    work.go.set()
    until(done.is_set, "첫 계산이 끝나지 않았다")
    assert value.read() == ("old", "", False)
    work.go.clear()
    done.clear()
    time.sleep(0.25)
    assert value.read() == ("old", "", True), "오래된 값을 주는 동안에도 다시 계산 중이라고 알린다"
    assert value.read() == ("old", "", True) and work.starts == 2, "계산은 한 번에 하나"
    work.go.set()
    until(done.is_set, "두 번째 계산이 끝나지 않았다")
    assert value.read() == ("new", "", False)


def test_a_computation_begun_before_forget_is_not_kept():
    work, done = Held("before the change", "after it"), threading.Event()
    value = refresh.Refresh("t", work, ttl=60, retry=60, done=done.set)
    assert value.read() == (None, "", True)
    until(lambda: work.starts == 1, "계산이 시작하지 않았다")
    value.forget()   # the wiring changed while the check read the old one
    work.go.set()
    until(done.is_set, "계산이 끝나지 않았다")
    assert value.read() == (None, "", True), "옛 결과는 버리고 다시 계산한다"
    until(lambda: work.starts == 2 and work.ended.is_set() and not work.answers, "다시 계산하지 않았다")
    until(lambda: value.read()[0] == "after it", "새 결과가 오지 않았다")


def test_a_failure_stands_its_retry_then_is_tried_again():
    work = Held(RuntimeError("Codex 응답 시간 초과: initialize"), ["model"])
    work.go.set()
    value = refresh.Refresh("t", work, ttl=60, retry=0.2)
    value.read()
    until(lambda: value.read() == (None, "Codex 응답 시간 초과: initialize", False), "실패를 알리지 않았다")
    assert work.starts == 1, "실패 직후에는 다시 하지 않는다"
    time.sleep(0.25)
    value.read()
    until(lambda: value.read() == (["model"], "", False), "다시 시도하지 않았다")


def test_a_thread_that_cannot_start_leaves_the_next_read_free():
    work = Held("value")
    work.go.set()
    value = refresh.Refresh("t", work, ttl=60, retry=60)
    with patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
        with pytest.raises(RuntimeError):
            value.read()
    assert value.read()[2], "다음 읽기가 계산을 시작한다"
    until(lambda: value.read() == ("value", "", False), "계산이 끝나지 않았다")


def test_a_computation_a_shutdown_cut_is_no_answer_for_the_next_lifespan():
    """Its Codex refused by the gate, the old check would hand the next server a shutdown's refusal."""

    from agent import chat_local

    work, done = Held(RuntimeError("서버가 꺼지는 중이라 Codex 를 띄우지 않는다"), "fresh"), threading.Event()
    value = refresh.Refresh("t", work, ttl=60, retry=60, done=done.set)
    value.read()
    until(lambda: work.starts == 1, "계산이 시작하지 않았다")
    chat_local.end_servers()
    chat_local.reopen_servers()
    work.go.set()
    until(done.is_set, "계산이 끝나지 않았다")
    assert value.read()[1:] == ("", True), "옛 회차의 거절은 버리고 다시 계산한다"
    until(lambda: value.read() == ("fresh", "", False), "새 회차에서 다시 계산하지 않았다")


def test_nothing_starts_once_the_server_is_going_down(monkeypatch):
    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(runtime, "stopping", stopping)
    work = Held("value")
    assert refresh.Refresh("t", work, ttl=60, retry=60).read() == (None, "", False) and work.starts == 0


def test_a_caller_waiting_on_a_failed_model_fetch_takes_its_failure():
    """Two callers behind one fetch that timed out would otherwise wait two whole Codex deadlines."""

    channels.codex_models.cache_clear()
    fetch = Held(RuntimeError("Codex 응답 시간 초과: initialize"), RuntimeError("never asked"))
    try:
        with patch.object(channels, "_fetch_codex_models", fetch):
            first = threading.Thread(target=lambda: pytest.raises(RuntimeError, channels.codex_models))
            first.start()
            until(lambda: fetch.starts == 1, "첫 요청이 시작하지 않았다")
            waited = []
            second = threading.Thread(target=lambda: waited.append(pytest.raises(RuntimeError, channels.codex_models)))
            second.start()
            time.sleep(0.1)
            fetch.go.set()
            first.join(5)
            second.join(5)
            assert "시간 초과" in str(waited[0].value) and fetch.starts == 1, "기다린 요청은 같은 실패를 받는다"
            with pytest.raises(RuntimeError, match="never asked"):
                channels.codex_models()   # a later call is a fresh try
    finally:
        channels.codex_models.cache_clear()


def test_a_waiting_caller_never_takes_a_retired_lifespan_s_failure():
    """The old fetch failed on the shutdown gate; the next server's caller asks Codex itself."""

    from agent import chat_local

    channels.codex_models.cache_clear()
    fetch = Held(RuntimeError("서버가 꺼지는 중이라 Codex 를 띄우지 않는다"), [{"id": "gpt"}])
    try:
        with patch.object(channels, "_fetch_codex_models", fetch):
            old = threading.Thread(target=lambda: pytest.raises(RuntimeError, channels.codex_models))
            old.start()
            until(lambda: fetch.starts == 1, "옛 요청이 시작하지 않았다")
            chat_local.end_servers()
            chat_local.reopen_servers()
            got = []
            current = threading.Thread(target=lambda: got.append(channels.codex_models()))
            current.start()
            time.sleep(0.1)
            fetch.go.set()
            old.join(5)
            current.join(5)
            assert got == [[{"id": "gpt"}]] and fetch.starts == 2, "옛 회차의 실패를 넘겨받지 않는다"
    finally:
        channels.codex_models.cache_clear()
