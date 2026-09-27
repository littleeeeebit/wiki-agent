"""`python tool/eval/decisions.py [eval/jev/smoke.json] [--out <file>] [--tape <query id> <file>]`

The live sample of stage 6's gate: the decision workflow, run for real —
Jev, the translator and a cold index of the manifest's synthetic corpus —
on its English and Korean questions, and on three runs made to end
otherwise: a key the service refuses, a run cancelled before it starts, a
run with one request allowed. Records each run's status, reason,
transitions, decisions and cost under `raw/eval/jev/`.

Every state and question sent to Jev is checked on its way out: none may be
Korean prose, since the decision path reads English only. `--tape` also
writes the tape of one query's run, for a replay fixture; its corpus is the
manifest's synthetic text.

Sends requests to TypeSafe and to the translator: run it when a live check
is wanted, not in the test suite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["WIKI_SEARCH"] = "off"   # the synthetic corpus is indexed here, never by the machine's daemon
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
import search  # noqa: E402
from common.language import language  # noqa: E402
from eval.baseline import RAW, load, materialize, revision  # noqa: E402
from main import knowledge  # noqa: E402

RESULT = "jev-decisions-result/1"
MANIFEST = search.HUB / "eval" / "jev" / "smoke.json"


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def watched(korean: list[str]):
    """The transport, with every string it sends checked for Korean prose."""

    real = decision.evaluate

    def evaluate(cfg, state, questions, trace, budget=None, stage=""):
        korean.extend(s for s in strings(state) + strings(questions) if language(s) == "ko")
        return real(cfg, state, questions, trace, budget, stage)

    return real, evaluate


def summary(query: dict | None, name: str, out: dict) -> dict:
    return {"id": query["id"] if query else name, "language": query["language"] if query else "en",
            "kind": query["kind"] if query else name, "status": out["status"], "reason": out["reason"],
            "direct": out["direct"], "path": [t["to"] for t in out["transitions"]],
            "missing": out["missing"], "sources": out["sources"], "split": out["split"],
            "requirements": [{k: r[k] for k in ("id", "text", "verdict", "score")} for r in out["requirements"]],
            "evidence": [e["locator"].get("path") for e in out["evidence"]],
            "conflicts": len(out["conflicts"]), "untrusted": len(out["untrusted"]),
            "decisions": [{k: d[k] for k in ("kind", "status", "reason_code", "verdicts", "model", "usage")}
                          for d in out["decisions"]],
            "normalization": out["normalization"], "budget": out["budget"]["used"],
            "elapsed_ms": out["budget"]["elapsed_ms"]}


def run(manifest_path: Path, tape_for: str | None = None) -> tuple[dict, dict | None]:
    manifest = load(manifest_path)
    cfg = decision.config()
    if cfg.mode == "off" or not cfg.key:
        raise SystemExit(f"Jev is not configured: {cfg.status()}")
    active = decision.Config("active", cfg.model, cfg.key_source, key=cfg.key)
    korean: list[str] = []
    real, decision.evaluate = watched(korean)
    rows, tape = [], None
    try:
        with tempfile.TemporaryDirectory(prefix="jev-decisions-") as scratch:
            hub, repo = materialize(Path(scratch), manifest["corpus"], None)
            search.HUB = knowledge.HUB = hub
            for query in manifest["queries"]:
                out = knowledge.prepare(query["text"], repo, cfg=active, record=query["id"] == tape_for, cache=None)
                if query["id"] == tape_for:
                    # The scratch folder names this machine and its user; a fixture names neither.
                    text = json.dumps(out["tape"], ensure_ascii=False)
                    for spelling in (str(Path(scratch)), Path(scratch).as_posix()):
                        text = text.replace(json.dumps(spelling)[1:-1], "<corpus>")
                    tape = json.loads(text)
                rows.append(summary(query, query["id"], out))
            ask = "Which port does the search daemon listen on?"
            refused = decision.Config("active", cfg.model, "file", key="not-a-valid-key")
            rows.append(summary(None, "refused_key", knowledge.prepare(ask, repo, cfg=refused, cache=None)))
            cancel = threading.Event()
            cancel.set()
            rows.append(summary(None, "cancelled", knowledge.prepare(ask, repo, cfg=active, cancel=cancel,
                                                                     cache=None)))
            allowance = knowledge.QUESTION
            knowledge.QUESTION = {**allowance, "calls": 1}
            try:
                rows.append(summary(None, "one_request", knowledge.prepare(ask, repo, cfg=active, cache=None)))
            finally:
                knowledge.QUESTION = allowance
    finally:
        decision.evaluate = real
    outcomes = sorted({f"{r['status']}{' (direct)' if r['direct'] else ''}" for r in rows})
    usage = [d["usage"] or {} for r in rows for d in r["decisions"]]
    return {"schema": RESULT, "manifest": {"id": manifest["id"], "version": manifest["version"]},
            "run": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **revision()},
            "jev": {"model": cfg.model, "policy": decision.policy(cfg.model, prompt_version=knowledge.PROMPT_VERSION)
                    .record(), "prompt_version": knowledge.PROMPT_VERSION},
            "outcomes": outcomes, "korean_sent": korean,
            "usage": {"requests": len(usage), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                      "output_tokens": sum(u.get("output_tokens", 0) for u in usage)},
            "runs": rows}, tape


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/decisions.py", description="Live decision sample")
    parser.add_argument("manifest", type=Path, nargs="?", default=MANIFEST)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--tape", nargs=2, metavar=("QUERY", "FILE"), default=None)
    args = parser.parse_args(argv)
    result, tape = run(args.manifest, args.tape[0] if args.tape else None)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RAW / f"decisions-{result['manifest']['id']}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.tape:
        if tape is None:
            parser.error(f"no query {args.tape[0]!r} in the manifest")
        Path(args.tape[1]).write_text(json.dumps(tape, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "outcomes": result["outcomes"], "korean_sent": len(result["korean_sent"]),
                      "usage": result["usage"],
                      "runs": [{k: r[k] for k in ("id", "language", "status", "reason", "direct", "missing")}
                               for r in result["runs"]]}, ensure_ascii=False, indent=1))
    return 1 if result["korean_sent"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
