"""Each question's run as a Langfuse trace, through the Python SDK (v4, OpenTelemetry).

What an answer rests on lies in what Jev was sent, what the drafting session
wrote and its recorded provenance. Explicit claim checks also record why a
claim was withheld. A later run may not reproduce it, so `knowledge.Run`
opens one trace when it starts,
its id the run's own, and records each step under it as it happens:

    answer-question (chain)           the question in, the published answer out
      retrieve-evidence (retriever)   one per retrieval; a repair may add one
        normalize-to-english (tool)   the texts sent to the translator, the outcomes
        route-question, grade-evidence, choose-repair (generation)   each Jev request
      draft-answer (generation)       the brief the host was sent, the text it wrote
      record-provenance (evaluator)  ordinary synthesis and its attribution
      verify-claims (evaluator)      explicit claim checks only
        judge-claims (generation)    Jev's requests during explicit verification

Observations are made from their parent object, never from the active
OpenTelemetry context: a run's work crosses threads (`Flow.outside`, the
app's run thread) and generator yields (`grounded`).

It goes to the self-hosted Langfuse the hub's `.env` names:
`LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
`LANGFUSE_TRACING_ENVIRONMENT` if set. Full text goes, private memories
included: the owner chose that, for a Langfuse on this machine. The Jev key
never does: everything passes the run's `redact` first (`Run.open`).

Fails open: without the SDK or the keys nothing is traced, and a failure in
tracing costs the trace, never the answer.

The four questions of `craft/client-lifecycle-in-one-scope`: one client per
process, made by the first run that finds the keys (`client`); shared by
every run of the process; flushed and shut down by the SDK's own exit hook,
and `flush` — bounded, after the answer is shown — for a command line before
it exits; owned by the process, so new keys take a restart.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections import Counter

import decision
from common import settings

try:
    from langfuse import Langfuse, propagate_attributes
except ImportError:   # the hooks' install has no SDK: no trace there
    Langfuse = propagate_attributes = None

# What each Jev request is named in the trace, by its decision kind: stable, verb first.
DECISIONS = {"route": "route-question", "judge": "grade-evidence", "repair": "choose-repair",
             "verify": "judge-claims"}

FLUSH_SECONDS = 5.0
_client = None
_secret = None   # the secret key `_client` was made with
_made = False
_lock = threading.Lock()


def config() -> dict | None:
    """The client's settings from the hub's `.env`; `None` when a key is missing."""

    try:
        found = settings.entries(decision.env_file())
    except (OSError, ValueError):
        return None
    base, public, secret, environment = (settings.pick(found, name)[0] for name in (
        "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_TRACING_ENVIRONMENT"))
    if not (base and public and secret):
        return None
    return {"base_url": base, "public_key": public, "secret_key": secret, "environment": environment or None}


def secrets() -> list[str]:
    """Langfuse's secret keys, for a run to keep out of what it records and
    traces: the one configured now, and the one the process's client was
    made with — a key edited in `.env` reaches the client only on restart."""

    where = config()
    return [k for k in {(where or {}).get("secret_key"), _secret} if k]


def client():
    """The process's Langfuse client, made on first use; `None` when tracing is off."""

    global _client, _made, _secret
    with _lock:
        if not _made:
            _made = True
            where = config() if Langfuse is not None else None
            if where is not None:
                _secret = where["secret_key"]
                try:
                    _client = Langfuse(**where)
                except Exception:  # noqa: BLE001 — no trace, never no answer
                    _client = None
        return _client


def flush(seconds: float = FLUSH_SECONDS) -> None:
    """Send what is buffered, waiting `seconds` at most: a command line calls
    this after it has shown its answer and before it exits. The SDK's flush
    waits on the exporter's own timeouts and retries, so it runs on a daemon
    thread the caller stops waiting for (review round 1 of #43)."""

    if _client is None:
        return

    def send() -> None:
        try:
            _client.flush()
        except Exception:  # noqa: BLE001
            pass

    worker = threading.Thread(target=send, daemon=True)
    worker.start()
    worker.join(seconds)


def usage(tokens: dict | None) -> dict | None:
    """Token counts as Langfuse reads them, from Jev's `input_tokens`/`output_tokens`
    or a host session's `in`/`out`/`cache_read`/`cache_write`."""

    # A provider's malformed usage is no count: telemetry never costs the answer.
    tokens = tokens if isinstance(tokens, dict) else {}
    pairs = {"input": tokens.get("input_tokens", tokens.get("in")),
             "output": tokens.get("output_tokens", tokens.get("out")),
             "cache_read_input_tokens": tokens.get("cache_read"),
             "cache_creation_input_tokens": tokens.get("cache_write")}
    # A count that is not finite is no count: `int()` of it raises, and telemetry never costs the answer.
    got = {k: int(v) for k, v in pairs.items() if isinstance(v, (int, float)) and math.isfinite(v)}
    return got or None


# ---- call records (reliability PR 4) ---------------------------------------------------
#
# One record per operation that may cost something: a Jev request, a translation, a
# host turn. A transport batch asking several questions is one call with several
# purposes, never several bills; a cache hit is a call record whose provider is
# `cache`, which costs nothing. A cost the provider does not report is `None` with
# `cost_known` false — unknown, never zero. Records hold digests, ids and counts,
# never a prompt or a passage: they go where the run's events go, and an export
# keeps them whole.

CALL = "call-record/1"
PURPOSES = ("route", "source_select", "grade", "sufficiency", "support", "normalize", "decompose", "draft",
            "explain", "graph_extract", "research")
# The purpose of each Jev question kind (`decision.policy.KINDS`).
PURPOSE_OF = {"route": "route", "analysis": "route", "progress": "route", "ask": "route", "action": "route",
              "source": "source_select",
              "useful": "grade", "conflict": "grade", "redirect": "grade", "coverage": "sufficiency",
              "repair": "sufficiency", "relation": "support", "answers": "support", "faithful": "support"}


def digest(value) -> str:
    """A short fingerprint of what a call was sent, for telling two calls apart without keeping either."""

    body = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def call(purposes, owner: str, provider: str | None, *, model: str | None = None, parent: str | None = None,
         sent=None, state_revision: str | None = None, elapsed_ms: int | None = None, tokens: dict | None = None,
         cost_usd=None, retry_of: str | None = None, outcome: str = "ok", **extra) -> dict:
    """One call record. `sent` is fingerprinted, never kept."""

    purposes = sorted(set([purposes] if isinstance(purposes, str) else purposes))
    if not purposes or set(purposes) - set(PURPOSES):
        raise ValueError(f"purposes are some of {PURPOSES}")
    known = provider == "cache" or (isinstance(cost_usd, (int, float)) and math.isfinite(cost_usd))
    return {"schema_version": CALL, "call_id": uuid.uuid4().hex, "parent_call_id": parent, "purpose": purposes,
            "owner": owner, "provider": provider, "model": model,
            "input_digest": None if sent is None else digest(sent), "state_revision": state_revision,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "duration_ms": elapsed_ms,
            "token_usage": usage(tokens), "cost_usd": 0.0 if provider == "cache" else cost_usd if known else None,
            "cost_known": known, "retry_of": retry_of, "outcome": outcome, **extra}


def jev_call(req: dict, res: dict, *, parent: str | None = None, state_revision: str | None = None,
             retry_of: str | None = None) -> dict | None:
    """The call record of one DecisionRequest and its checked result: one
    call, whatever number of questions it asked, with the tokens a failed
    one spent too. Jev reports tokens, not money. `None` for a request
    that never went out (no key, no slot, stopped first): no call."""

    cached = bool(res.get("cached"))
    if not cached and not res.get("sent"):
        return None
    return call({PURPOSE_OF[q["decision"]] for q in req["questions"].values()}, "jev", "cache" if cached else "jev",
                model=res.get("model") or req.get("model"), parent=parent,
                sent={"state": req["state_en"], "questions": req["questions"]}, state_revision=state_revision,
                elapsed_ms=res.get("elapsed_ms"), tokens=None if cached else res.get("usage"), retry_of=retry_of,
                outcome=res["status"], request_id=req["request_id"])


def fallback_call(req: dict, res: dict, *, parent: str | None = None,
                  state_revision: str | None = None) -> dict | None:
    """The call record of the host turn that settled what one
    DecisionRequest left uncertain (`decision.contract.asked`), with how many
    questions it was asked and settled; `None` when no fallback ran."""

    host = res.get("fallback")
    if not host:
        return None
    return call({PURPOSE_OF[req["questions"][n]["decision"]] for n in host["asked"]}, "jev_fallback", "host",
                model=host.get("model"), parent=parent,
                sent={"state": req["state_en"], "questions": {n: req["questions"][n] for n in host["asked"]}},
                state_revision=state_revision, elapsed_ms=host.get("elapsed_ms"), cost_usd=host.get("cost_usd"),
                outcome="failed" if host.get("error") else "ok", request_id=req["request_id"],
                asked=len(host["asked"]), settled=len(host["answers"]))


def totals(calls: list[dict]) -> dict:
    """What a run's calls add up to: the known cost, and how many calls' cost is unknown.
    A record from before these fields existed counts as unknown."""

    paid = [c for c in calls if c.get("provider") != "cache"]
    tokens = {k: sum((c.get("token_usage") or {}).get(k, 0) for c in calls) for k in ("input", "output")}
    return {"calls": len(calls), "provider_calls": len(paid), "cache_hits": len(calls) - len(paid),
            "cost_usd_known": round(sum(c["cost_usd"] for c in paid if c.get("cost_known")), 6),
            "cost_unknown": sum(not c.get("cost_known") for c in paid), "tokens": tokens,
            "by_purpose": dict(Counter(p for c in calls for p in c.get("purpose") or ["unknown"]))}


def root(run_id: str, question: str, tags: list[str], metadata: dict):
    """The run's root observation, its trace id the run's id; `None` when tracing is off."""

    lf = client()
    if lf is None:
        return None
    try:
        with propagate_attributes(trace_name="answer-question", tags=tags):
            return lf.start_observation(trace_context={"trace_id": run_id}, name="answer-question",
                                        as_type="chain", input=question, metadata=metadata)
    except Exception:  # noqa: BLE001
        return None


def child(parent, tags: list[str], name: str, kind: str, **fields):
    """An observation under `parent`, carrying the trace's name and tags as
    the root does: v4 keeps them per observation, and a child made from its
    parent object — on another thread, past a generator's yield — sits outside
    the root's `propagate_attributes`, so a filter on them would miss it."""

    with propagate_attributes(trace_name="answer-question", tags=tags):
        return parent.start_observation(name=name, as_type=kind, **fields)
