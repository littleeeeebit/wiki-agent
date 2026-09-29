"""DecisionRequest and DecisionResult: typed questions in, validated answers out.

A request carries its English state, its questions — each with the decision
kind whose policy reads it, and the candidate it is about — and the only
candidate ids a result may name. `decide` sends it (or finds it in a
`Cache`), checks every answer again, classifies each by the policy, and
returns a result whose status keeps failures apart:

    decided      every answer valid, none uncertain under the policy
    uncertain    every answer valid, at least one uncertain
    unavailable  no answer: no key, auth, quota, network, timeout, busy ...
    invalid      an answer missing, unknown, of the wrong shape, or naming
                 a candidate that was not offered
    cancelled    the run was cancelled
    exhausted    the run's budget was spent

None of the last four is a negative answer, and none carries answers.
`checked` is what a caller runs before using a result: the request id,
the schema and the candidates must be the request's.

An answer the policy leaves uncertain may go to a `fallback`: the host model
answers the same questions over the same state (reliability PR 5, v2). A kind
whose uncertain verdict code settles safely (`policy.CODE_SETTLES`) never goes.
Jev's answers stay as they came; what the host settled sits beside them
under `fallback`, its verdicts replace the uncertain ones, and `final`
reads a question's settled answer. The host is not Jev: a caller marks
what the host settled, and nothing it settled is counted as Jev's.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable

from common.budget import Budget, Cancelled, Exhausted

from . import JevError, distribution, malformed, unit
from .policy import CHOICE, CODE_SETTLES, KINDS, NOUL, Policy, verdict

REQUEST = "decision-request/1"
RESULT = "decision-result/1"
STATUSES = ("decided", "uncertain", "unavailable", "invalid", "cancelled", "exhausted")
# The option a Choice offers for "none of these clearly": code decides instead.
DEFER = "defer"
INVALID = ("invalid_response", "response_too_large", "unsupported_question")

# `(state, questions, trace, budget, stage) -> {name: answer}`: `decision.evaluate` bound to a config.
Evaluate = Callable[..., dict]
# The host model asked what Jev left uncertain (`main.knowledge.host_decides`).
# Called as `(state, questions, stage)`, it returns `answers` ({name: "yes" | "no" | option | "unsure"}),
# `model`, `cost_usd` and `elapsed_ms`, and `error` when the turn failed.
Fallback = Callable[..., dict]
UNSURE = "unsure"


def request(kind: str, state_en: dict, questions: dict[str, dict], *, allowed: list[str], model: str,
            prompt_version: str, policy_version: str, normalization_version: str, budget: Budget,
            trace_id: str = "") -> dict:
    """A DecisionRequest.

    `questions` maps a name to its `decision` (the policy kind),
    its `question` (a transport Noul or Choice),
    and its `candidate` (the id it is about, or `None`).
    A Choice's options must be offered candidates, or `DEFER`.
    `ValueError` for a request that breaks this — a caller's bug, never sent."""

    allowed = list(dict.fromkeys(allowed))
    for name, q in questions.items():
        if q.get("decision") not in KINDS or malformed(q.get("question")):
            raise ValueError(f"question {name}: unknown decision or malformed question")
        expected = "noul" if q["decision"] in NOUL else "choice"
        if q["question"]["type"] != expected:
            raise ValueError(f"question {name}: {q['decision']} is a {expected}")
        if q.get("candidate") is not None and q["candidate"] not in allowed:
            raise ValueError(f"question {name}: candidate not offered")
        if q["decision"] in CHOICE and not set(q["question"]["criteria"]) <= set(allowed) | {DEFER}:
            raise ValueError(f"question {name}: an option is not an offered candidate")
    used = budget.used
    return {"schema_version": REQUEST, "request_id": uuid.uuid4().hex, "decision_kind": kind,
            "state_en": state_en, "questions": questions, "allowed_candidate_ids": allowed,
            "model": model, "prompt_version": prompt_version, "policy_version": policy_version,
            "normalization_version": normalization_version,
            "deadline": time.time() + budget.left(),
            "remaining_budget": {"seconds": round(budget.left(), 3),
                                 "calls": budget.limits["calls"] - used["calls"],
                                 "candidates": budget.limits["candidates"] - used["candidates"],
                                 "tokens": None if budget.limits["tokens"] is None
                                 else budget.limits["tokens"] - used["tokens"]},
            "trace_id": trace_id}


def shaped(question: dict, value: object) -> bool:
    """`value` is what the transport returns for `question`, exactly: a bool
    is not a probability and a string is not a number."""

    if question["type"] == "noul":
        return unit(value)
    if not isinstance(value, dict) or not unit(value.get("confidence")):
        return False
    if question["type"] == "choice":
        return value.get("choice") in question["criteria"] and distribution(value.get("probabilities"),
                                                                            question["criteria"])
    levels = len(question["criteria"])
    score = value.get("score")
    return (type(score) in (int, float) and math.isfinite(score) and 0 <= score <= levels - 1
            and distribution(value.get("probabilities"), [str(n) for n in range(levels)]))


def result(req: dict, status: str, reason: str = "", answers: dict | None = None, verdicts: dict | None = None,
           selected: list[str] = (), call: dict | None = None, elapsed: float = 0.0, cached: bool = False,
           fallback: dict | None = None) -> dict:
    call = call or {}
    return {"schema_version": RESULT, "request_id": req["request_id"], "decision_kind": req["decision_kind"],
            "status": status, "answers": answers or {}, "verdicts": verdicts or {},
            "selected_candidate_ids": list(selected), "reason_code": reason,
            "model": call.get("model"), "policy_version": req["policy_version"], "usage": call.get("usage"),
            "elapsed_ms": round(elapsed * 1000), "trace_id": req["trace_id"], "cached": cached,
            "sent": bool(call.get("sent")) and not cached, "fallback": fallback}


def settled(question: dict, said: object) -> dict | float | None:
    """The host's word on `question` as an answer the policy accepts outright,
    or `None` when it said nothing usable: `unsure`, or an option not offered."""

    if question["type"] == "noul":
        return {"yes": 1.0, "no": 0.0}.get(said)
    if said not in question["criteria"] or said == DEFER:
        return None
    return {"choice": said, "confidence": 1.0,
            "probabilities": {c: float(c == said) for c in question["criteria"]}}


def final(res: dict, name: str) -> dict | float:
    """What question `name` came to: the host's settled answer where the
    fallback settled it, else Jev's."""

    return ((res.get("fallback") or {}).get("answers") or {}).get(name, res["answers"][name])


def settled_by(res: dict, name: str) -> str:
    """`host` when the fallback settled question `name`, else `jev`."""

    return "host" if name in ((res.get("fallback") or {}).get("answers") or {}) else "jev"


def decide(req: dict, evaluate: Evaluate, budget: Budget, trace: list[dict], pol: Policy,
           cache: Cache | None = None, fallback: Fallback | None = None) -> dict:
    """The DecisionResult for `req`. Never raises for a failed decision: the
    failure is its status and reason. The policy must be the one the request
    names. With a `fallback`, the questions the policy left uncertain go to
    it once, together; a fallback returns its failure as a value, never raises."""

    if pol.version != req["policy_version"]:
        raise ValueError("the request names another policy")
    started = time.monotonic()
    questions = {name: q["question"] for name, q in req["questions"].items()}
    hit = cache.get(req) if cache else None
    call: dict = {}
    if hit is not None:
        answers, call = hit
    else:
        mark = len(trace)

        def spent() -> dict:
            # What the request cost, failed or not: sent as its entry says (`evaluate`
            # marks the transport's dispatch), else — a stand-in evaluator — once it reported usage.
            entry = trace[mark] if len(trace) > mark else {}
            return {"model": entry.get("model"), "usage": entry.get("usage"),
                    "sent": bool(entry["sent"] if "sent" in entry else entry.get("usage"))}

        try:
            answers = evaluate(req["state_en"], questions, trace, budget, req["decision_kind"])
        except (Cancelled, Exhausted, JevError) as exc:
            reason = getattr(exc, "category", "")
            status = ("cancelled" if isinstance(exc, Cancelled) or reason == "cancelled"
                      else "exhausted" if isinstance(exc, Exhausted)
                      else "invalid" if reason in INVALID else "unavailable")
            return result(req, status, str(exc) if isinstance(exc, Exhausted) else reason, call=spent(),
                          elapsed=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 — a broken transport is no decision, never a negative one
            return result(req, "unavailable", f"error:{type(exc).__name__}", call=spent(),
                          elapsed=time.monotonic() - started)
        call = spent()
    if not isinstance(answers, dict) or set(answers) != set(questions):
        return result(req, "invalid", "missing_or_unknown_answer", call=call, elapsed=time.monotonic() - started)
    if not all(shaped(questions[name], answers[name]) for name in questions):
        return result(req, "invalid", "malformed_answer", call=call, elapsed=time.monotonic() - started)
    verdicts = {name: verdict(pol, q["decision"], answers[name]) for name, q in req["questions"].items()}
    if hit is None and cache:
        cache.put(req, answers, call)
    doubt = [name for name, v in verdicts.items()
             if v == "uncertain" and req["questions"][name]["decision"] not in CODE_SETTLES]
    host = None
    if doubt and fallback is not None:
        began = time.monotonic()
        host = asked(fallback, req, doubt)
        # The host's turn has an allowance of its own, as a drafting turn has: Jev's deadline moves past it.
        budget.aside(time.monotonic() - began)
    for name, answer in (host or {}).get("answers", {}).items():
        verdicts[name] = verdict(pol, req["questions"][name]["decision"], answer)
    selected = []
    for name, q in req["questions"].items():
        if verdicts[name] != "yes":
            continue
        if q["decision"] in CHOICE:
            selected.append(((host or {}).get("answers") or {}).get(name, answers[name])["choice"])
        elif q["candidate"] is not None:
            selected.append(q["candidate"])
    status = "uncertain" if "uncertain" in verdicts.values() else "decided"
    return result(req, status, "", answers, verdicts, list(dict.fromkeys(selected)), call,
                  time.monotonic() - started, cached=hit is not None, fallback=host)


def asked(fallback: Fallback, req: dict, doubt: list[str]) -> dict:
    """What the host settled of the questions in `doubt`: `asked` (their
    names), `said` (its word on each), `answers` (the ones it settled, as
    answers the policy accepts), and the call's model, cost and time."""

    questions = {name: req["questions"][name]["question"] for name in doubt}
    got = fallback(req["state_en"], questions, req["decision_kind"])
    said = got.get("answers") or {}
    answers = {name: a for name in doubt if (a := settled(questions[name], said.get(name))) is not None}
    return {"by": "host", "asked": doubt, "said": {name: said.get(name) for name in doubt}, "answers": answers,
            **{k: got[k] for k in ("model", "cost_usd", "elapsed_ms", "error") if k in got}}


def checked(req: dict, res: dict) -> dict:
    """`res`, once it is shown to answer `req`: same schema, same request id,
    only offered candidates, and answers for exactly the questions asked.
    `ValueError` otherwise, so nothing mismatched reaches an executor."""

    if res.get("schema_version") != RESULT or req.get("schema_version") != REQUEST:
        raise ValueError("schema_version")
    if res.get("request_id") != req["request_id"]:
        raise ValueError("the result answers another request")
    if not set(res["selected_candidate_ids"]) <= set(req["allowed_candidate_ids"]):
        raise ValueError("the result names a candidate that was not offered")
    if res["status"] in ("decided", "uncertain") and set(res["answers"]) != set(req["questions"]):
        raise ValueError("the answers are not the questions asked")
    if res["status"] not in ("decided", "uncertain") and res["answers"]:
        raise ValueError("a failed decision carries answers")
    return res


class Cache:
    """Answers by complete decision identity: the state, the questions and
    their candidates, and the model, prompt, policy and normalization
    versions. A changed source, candidate set or revision is another state,
    so another key. Only answers are kept — a failure is not a negative.
    Bounded; the oldest goes first."""

    def __init__(self, size: int = 256):
        self.size = size
        self._items: OrderedDict[str, tuple[dict, dict]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(req: dict) -> str:
        body = json.dumps([req[k] for k in ("decision_kind", "state_en", "questions", "allowed_candidate_ids",
                                            "model", "prompt_version", "policy_version",
                                            "normalization_version")], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def get(self, req: dict) -> tuple[dict, dict] | None:
        with self._lock:
            key = self.key(req)
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, req: dict, answers: dict, call: dict) -> None:
        with self._lock:
            self._items[self.key(req)] = (answers, call)
            while len(self._items) > self.size:
                self._items.popitem(last=False)
