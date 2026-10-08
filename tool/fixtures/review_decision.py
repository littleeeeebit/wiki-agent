"""Typed Jev stand-in; API/browser commands remain real in integration tests."""

import json
from functools import partial

import decision

from main.decisions import HANGUL


def normalized(state, seconds, **kwargs):
    return json.loads(HANGUL.sub("fixture", json.dumps(state, ensure_ascii=False))), "fixture"


def transport(cfg):
    return evaluate if cfg.model == "fixture-jev" else partial(decision.evaluate, cfg)


def evaluate(state, questions, trace, budget, stage):
    budget.call()
    trace.append({"model": "fixture-jev", "sent": True})
    answers = {}
    for name, question in questions.items():
        offered = question["criteria"]
        choice = "run" if "run" in offered else "covered"
        answers[name] = {"choice": choice, "confidence": 1.0,
                         "probabilities": {key: float(key == choice) for key in offered}}
    return answers
