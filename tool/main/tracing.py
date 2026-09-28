"""Each question's run as a Langfuse trace, through the Python SDK (v4, OpenTelemetry).

Why an answer was withheld lies in what Jev was sent, what the drafting
session wrote and how each claim was judged — and a run reproduced later may
not reproduce it. So a run (`knowledge.Run`) opens one trace when it starts,
its id the run's own, and records each step under it as it happens:

    answer-question (chain)           the question in, the published answer out
      retrieve-evidence (retriever)   one per retrieval; a repair may add one
        normalize-to-english (tool)   the texts sent to the translator, the outcomes
        route-question, grade-evidence, choose-repair (generation)   each Jev request
      draft-answer (generation)       the brief the host was sent, the text it wrote
      verify-claims (evaluator)       the draft, and each claim's check and support
        judge-claims (generation)     Jev's requests while verifying

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

import math
import threading

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

    tokens = tokens or {}
    pairs = {"input": tokens.get("input_tokens", tokens.get("in")),
             "output": tokens.get("output_tokens", tokens.get("out")),
             "cache_read_input_tokens": tokens.get("cache_read"),
             "cache_creation_input_tokens": tokens.get("cache_write")}
    # A count that is not finite is no count: `int()` of it raises, and telemetry never costs the answer.
    got = {k: int(v) for k, v in pairs.items() if isinstance(v, (int, float)) and math.isfinite(v)}
    return got or None


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
