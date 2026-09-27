"""The shared settings reader, the Jev configuration, transport and probe.

No test reads the hub's `.env` or the machine's key (`conftest.py` points
`JEV_ENV` away), and no test reaches the network: the connection is faked.
"""

import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import decision
from common import settings
from common.budget import Budget, Exhausted
from main import knowledge

TOOL = Path(__file__).resolve().parent


def env(tmp_path, monkeypatch, text):
    path = tmp_path / "jev.env"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(path))
    return path


# -- The reader -------------------------------------------------------------

def test_the_file_format_is_small_and_literal(tmp_path):
    path = tmp_path / ".env"
    path.write_text("﻿# a comment\n"
                    "  SPACED  =  value  \n"
                    'DOUBLE="has # hash"\n'
                    "SINGLE='x'\n"
                    "INLINE=abc # note\n"
                    "HASHED=abc#def\n"
                    "SHELL=$(whoami) ${HOME}\n"
                    "EMPTY=\n"
                    "DUP=first\nDUP=second\n"
                    "no equals sign\n"
                    "export EXPORTED=1\n", encoding="utf-8")
    assert settings.entries(path) == {
        "SPACED": "value", "DOUBLE": "has # hash", "SINGLE": "x", "INLINE": "abc", "HASHED": "abc#def",
        "SHELL": "$(whoami) ${HOME}", "EMPTY": "", "DUP": "first", "export EXPORTED": "1"}


def test_missing_is_empty_and_unreadable_raises(tmp_path):
    assert settings.entries(tmp_path / "absent") == {}
    with pytest.raises(OSError):
        settings.entries(tmp_path)   # a directory cannot be read as a file
    bad = tmp_path / "bad.env"
    bad.write_bytes(b"KEY=\xff\xfe\xfa")
    with pytest.raises(ValueError):
        settings.entries(bad)


def test_file_then_environment_then_default_and_blank_wins(monkeypatch):
    monkeypatch.setenv("NAME", " from-env ")
    assert settings.pick({"NAME": "from-file"}, "NAME") == ("from-file", "file")
    assert settings.pick({"NAME": ""}, "NAME") == ("", "file")
    assert settings.pick({}, "NAME") == ("from-env", "environment")
    monkeypatch.delenv("NAME")
    assert settings.pick({}, "NAME", "d") == ("d", "default")


# -- Configuration ----------------------------------------------------------

def test_the_file_key_is_used_and_never_enters_the_environment(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, "TYPESAFE_API_KEY=file-secret\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-secret")
    cfg = decision.config()
    assert cfg.key == "file-secret" and cfg.key_source == "file"
    assert os.environ["TYPESAFE_API_KEY"] == "env-secret"
    status = cfg.status()
    assert status == {"mode": "shadow", "model": "jev-1.13.0", "key": True, "key_source": "file",
                      "health": "configured", "problem": "", "file": "jev.env"}
    assert "secret" not in json.dumps(status) + repr(cfg)


def test_an_empty_file_key_does_not_fall_through_to_the_environment(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, "TYPESAFE_API_KEY=\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-secret")
    cfg = decision.config()
    assert cfg.key == "" and cfg.mode == "off" and cfg.status()["health"] == "disabled"


@pytest.mark.parametrize("text, mode, problem", [
    ("", "off", ""),
    ("TYPESAFE_API_KEY=k\n", "shadow", ""),
    ("TYPESAFE_API_KEY=k\nWIKI_JEV=on\n", "active", ""),
    ("TYPESAFE_API_KEY=k\nWIKI_JEV=on\nWIKI_JEV_MODE=shadow\n", "shadow", ""),
    ("TYPESAFE_API_KEY=k\nWIKI_JEV_MODE=OFF\n", "off", ""),
    ("TYPESAFE_API_KEY=k\nWIKI_JEV_MODE=\n", "shadow", ""),
    ("WIKI_JEV_MODE=active\n", "active", ""),
    ("TYPESAFE_API_KEY=k\nWIKI_JEV_MODE=loud\n", "off", "invalid_mode"),
])
def test_modes(tmp_path, monkeypatch, text, mode, problem):
    env(tmp_path, monkeypatch, text)
    cfg = decision.config()
    assert (cfg.mode, cfg.problem) == (mode, problem)


def test_the_old_switch_in_the_environment_still_means_active(monkeypatch):
    monkeypatch.setenv("WIKI_JEV", "on")
    assert decision.config().mode == "active"


def test_the_model_is_configurable(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, "WIKI_JEV_MODEL=jev-2\n")
    assert decision.config().model == "jev-2"


def test_unreadable_configuration_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_ENV", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-secret")
    status = decision.config().status()
    assert status["mode"] == "off" and status["health"] == "unavailable"
    assert status["problem"] == "unreadable_config" and not status["key"]


# -- Transport --------------------------------------------------------------

CFG = decision.Config("active", "jev-1.13.0", "file", key="test-key")


def connection(monkeypatch, status=200, payload=None, delay=0.0, fail=None):
    """A fake `HTTPSConnection`; the requests it saw."""

    sent = []

    class Connection:
        def __init__(self, host, timeout):
            assert host == "api.typesafe.ai"

        def request(self, method, path, body, headers):
            assert method == "POST" and path == "/v1/systemone"
            assert headers["Authorization"] == "Bearer test-key"
            sent.append(json.loads(body))
            if fail:
                raise fail

        def getresponse(self):
            time.sleep(delay)
            self.status = status
            return self

        def read(self, limit):
            return json.dumps(payload if payload is not None else {}).encode()

        def close(self):
            pass

    monkeypatch.setattr(decision, "resolve", lambda end, cancel: "192.0.2.1")
    monkeypatch.setattr(decision, "connection", lambda address, timeout: Connection(decision.HOST, timeout))
    return sent


def noul_payload(value):
    return {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": value}},
            "usage": {"input_tokens": 20, "output_tokens": 1}}


def test_a_noul_answer_is_returned_with_model_and_usage(monkeypatch):
    sent = connection(monkeypatch, payload=noul_payload(0.9))
    trace, budget = [], Budget(seconds=5, calls=2, candidates=0)
    assert decision.evaluate(CFG, {"query": "한글"}, {"q": decision.noul("Relevant?")}, trace, budget, "grade") \
        == {"q": 0.9}
    assert sent[0]["state"]["query"] == "한글" and sent[0]["model"] == "jev-1.13.0"
    assert trace[0]["stage"] == "grade" and trace[0]["model"] == "jev-1.13.0"
    assert trace[0]["usage"]["input_tokens"] == 20 and budget.used == {"calls": 1, "tokens": 21, "candidates": 0}
    assert "test-key" not in json.dumps(trace)


@pytest.mark.parametrize("value", [True, "0.9", -0.1, 1.1, math.nan, math.inf, None])
def test_invalid_probabilities_are_invalid_responses(monkeypatch, value):
    connection(monkeypatch, payload=noul_payload(value))
    with pytest.raises(decision.JevError) as caught:
        decision.evaluate(CFG, {}, {"q": decision.noul("Relevant?")}, [])
    assert caught.value.category == "invalid_response"


@pytest.mark.parametrize("drop", [
    {"model": None}, {"model": ""}, {"model": 7}, {"usage": None}, {"usage": {"input_tokens": 20}},
    {"usage": {"input_tokens": -1, "output_tokens": 1}}, {"usage": {"input_tokens": 1.5, "output_tokens": 1}},
])
def test_a_response_without_its_model_or_usage_is_invalid(monkeypatch, drop):
    """The stage 1 gate verifies the responding model and usage, so a 200
    that lacks either is not a reachable service answering properly."""

    payload = {**noul_payload(0.9), **drop}
    connection(monkeypatch, payload={k: v for k, v in payload.items() if v is not None})
    with pytest.raises(decision.JevError, match="invalid_response"):
        decision.evaluate(CFG, {}, {"q": decision.noul("Relevant?")}, [])
    connection(monkeypatch, payload={**PROBE_ANSWERS, **drop})
    out = decision.probe(CFG)
    assert out["health"] == "unavailable" and out["category"] == "invalid_response"


CHOICE = {"c": decision.choice("Which?", {"a": None, "b": None})}


@pytest.mark.parametrize("answer, valid", [
    ({"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8}, True),
    ({"type": "choice", "choice": "z", "probabilities": {"a": 0.9}, "confidence": 0.8}, False),
    ({"type": "choice", "choice": "a", "probabilities": {"z": 0.9}, "confidence": 0.8}, False),
    ({"type": "choice", "choice": "a", "probabilities": {"a": 0.9}, "confidence": 1.5}, False),
    ({"type": "noul", "noul": 0.9}, False),
])
def test_a_choice_must_name_an_offered_option(monkeypatch, answer, valid):
    connection(monkeypatch, payload={"model": "m", "answers": {"c": answer},
                                     "usage": {"input_tokens": 1, "output_tokens": 1}})
    if valid:
        assert decision.evaluate(CFG, {}, CHOICE, [])["c"]["choice"] == "a"
    else:
        with pytest.raises(decision.JevError, match="invalid_response"):
            decision.evaluate(CFG, {}, CHOICE, [])


@pytest.mark.parametrize("status, category", [(401, "auth_failed"), (403, "auth_failed"), (429, "quota"),
                                              (422, "invalid_request"), (529, "unavailable"), (418, "http_error")])
def test_http_failures_have_their_own_categories(monkeypatch, status, category):
    connection(monkeypatch, status=status)
    trace = []
    with pytest.raises(decision.JevError) as caught:
        decision.evaluate(CFG, {}, {"q": decision.noul("Relevant?")}, trace)
    assert caught.value.category == category and caught.value.status == status
    assert trace[0]["error"] == category and trace[0]["status"] == status


def test_missing_key_network_timeout_and_cancel_are_distinct(monkeypatch):
    q = {"q": decision.noul("Relevant?")}
    sent = connection(monkeypatch, payload=noul_payload(0.5))
    for cfg in (decision.Config("active", "m", "default"), decision.Config("active", "m", "file", key="")):
        with pytest.raises(decision.JevError, match="missing_api_key"):
            decision.evaluate(cfg, {}, q, [])
    assert sent == [], "a request went out without a key"

    connection(monkeypatch, fail=ConnectionRefusedError())
    with pytest.raises(decision.JevError, match="network"):
        decision.evaluate(CFG, {}, q, [])

    connection(monkeypatch, payload=noul_payload(0.5), delay=0.5)
    started = time.monotonic()
    with pytest.raises(decision.JevError, match="timeout"):
        decision.evaluate(CFG, {}, q, [], Budget(seconds=5, calls=2, candidates=0, call_seconds=0.05))
    assert time.monotonic() - started < 0.3

    budget = Budget(seconds=5, calls=2, candidates=0)
    threading.Timer(0.05, budget.cancel.set).start()
    started = time.monotonic()
    with pytest.raises(decision.JevError, match="cancelled"):
        decision.evaluate(CFG, {}, q, [], budget)
    assert time.monotonic() - started < 0.3


def test_the_call_count_is_shared(monkeypatch):
    connection(monkeypatch, payload=noul_payload(0.5))
    budget = Budget(seconds=5, calls=1, candidates=0)
    decision.evaluate(CFG, {}, {"q": decision.noul("Relevant?")}, [], budget)
    with pytest.raises(Exhausted, match="calls"):
        decision.evaluate(CFG, {}, {"q": decision.noul("Relevant?")}, [], budget)


def test_an_oversized_state_is_not_sent(monkeypatch):
    sent = connection(monkeypatch, payload=noul_payload(0.5))
    with pytest.raises(decision.JevError, match="state_too_large"):
        decision.evaluate(CFG, {"x": "y" * decision.MAX_BODY}, {"q": decision.noul("?")}, [])
    assert sent == []


# -- Probe ------------------------------------------------------------------

PROBE_ANSWERS = {"model": "jev-1.13.0", "usage": {"input_tokens": 50, "output_tokens": 2},
                 "answers": {"green_sky": {"type": "noul", "noul": 0.97},
                             "sky_color": {"type": "choice", "choice": "green", "confidence": 0.9,
                                           "probabilities": {"green": 0.95, "blue": 0.04, "red": 0.01}}}}


def test_the_probe_verifies_both_shapes_and_reports_without_the_key(monkeypatch):
    sent = connection(monkeypatch, payload=PROBE_ANSWERS)
    out = decision.probe(CFG)
    assert out["health"] == "reachable" and out["expected"] is True and out["category"] == ""
    assert out["model"] == "jev-1.13.0" and out["usage"]["input_tokens"] == 50
    assert out["answers"]["sky_color"]["choice"] == "green" and out["budget"]["used"]["calls"] == 1
    assert {q["type"] for q in sent[0]["questions"].values()} == {"noul", "choice"}
    assert "test-key" not in json.dumps(out)


@pytest.mark.parametrize("status, health, category", [(401, "auth_failed", "auth_failed"),
                                                      (429, "unavailable", "quota")])
def test_the_probe_names_the_failure(monkeypatch, status, health, category):
    connection(monkeypatch, status=status)
    out = decision.probe(CFG)
    assert (out["health"], out["category"], out["status"]) == (health, category, status)


def test_the_probe_without_a_key_sends_nothing(monkeypatch):
    sent = connection(monkeypatch, payload=PROBE_ANSWERS)
    out = decision.probe(decision.config())
    assert out["health"] == "unavailable" and out["category"] == "missing_api_key" and sent == []


def test_the_probe_cli_offline_reads_settings_and_sends_nothing(tmp_path):
    path = tmp_path / "jev.env"
    path.write_text("TYPESAFE_API_KEY=cli-secret\n", encoding="utf-8")
    run = subprocess.run([sys.executable, str(TOOL / "jev_probe.py")], capture_output=True, text=True,
                         encoding="utf-8", env={**os.environ, "JEV_ENV": str(path)}, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["health"] == "configured" and "cli-secret" not in run.stdout + run.stderr


# -- Composition ------------------------------------------------------------

def test_mode_off_sends_nothing_and_keeps_baseline_retrieval(monkeypatch):
    from test_decision_flow import chunk, found

    monkeypatch.setattr(knowledge, "run_round", lambda req, project, budget: found([chunk("a", "baseline")]))
    monkeypatch.setattr(decision, "evaluate", lambda *a, **kw: pytest.fail("a request in off mode"))
    monkeypatch.setattr(knowledge, "english", lambda *a, **kw: pytest.fail("a translation in off mode"))
    cfg = decision.Config("off", "m", "file", key="off-secret")
    out = knowledge.prepare("Question", None, cfg=cfg)
    assert out["status"] == "unavailable" and out["evidence"][0]["original_text"] == "baseline"
    assert out["reason"] == "disabled" and out["decisions"] == []
    assert out["jev"]["mode"] == "off" and "off-secret" not in json.dumps(out)
