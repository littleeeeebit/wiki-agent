"""`python tool/eval/answers.py [eval/jev/smoke.json] [--ids smoke-01 ...] [--model <model>] [--out <file>]`

The live sample of stage 7's gate: grounded answers, run for real — Jev,
the translator, a cold index of the manifest's synthetic corpus, and a
generating session of the host CLI — on the manifest's questions. Each
question is retrieved (`knowledge.prepare`), drafted, verified and published
(`knowledge.grounded`) exactly as the app's active mode does, with the
generator an isolated session holding the chat answer prompt and read-only
tools in the corpus. Records each answer's status, its accepted, rejected
and unresolved claims, the published text and the cost under `raw/eval/jev/`.

Every state sent to Jev is checked on its way out: none may be Korean
prose. Whether a published answer is right is for a person to read; the
calibration split (`tool/eval/policy.py --relation`) counts false
acceptance and false rejection.

Sends requests to TypeSafe, the translator and the host CLI: run it when a
live check is wanted, not in the test suite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["WIKI_SEARCH"] = "off"   # the synthetic corpus is indexed here, never by the machine's daemon
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
import search  # noqa: E402
from agent import ChatSession  # noqa: E402
from eval.baseline import RAW, load, materialize, revision  # noqa: E402
from eval.decisions import ALONE, alone, watched  # noqa: E402
from main import channels, knowledge  # noqa: E402

RESULT = "jev-answers-result/1"
MANIFEST = search.HUB / "eval" / "jev" / "smoke.json"
# A correct complete answer, the same in Korean, a bridge, a conflict, one the corpus cannot answer, a greeting.
DEFAULT = ("smoke-01", "smoke-02", "smoke-03", "smoke-04", "smoke-08", "smoke-09")


def drafting(chat: ChatSession, spent: dict):
    def generate(message: str):
        for ev in chat.say(message):
            if ev.kind == "error" or (ev.kind == "done" and ev.meta.get("error")):
                raise RuntimeError(ev.text or "the host turn failed")
            # What the host read with its own tools, beside the dossier: a grade rests on it too.
            if ev.kind == "tool" and ev.meta.get("tool") == "tool_result":
                spent.setdefault("read", []).append(ev.text)
            if ev.kind == "done":
                spent["turns"] = spent.get("turns", 0) + 1
                spent["cost_usd"] = spent.get("cost_usd", 0) + (ev.meta.get("cost_usd") or 0)
                return ev.text
        raise RuntimeError("the host turn ended without an answer")
        yield  # a generator, as `knowledge.grounded` asks

    return generate


def summary(query: dict, dossier: dict, out: dict, spent: dict) -> dict:
    v = out["verified"]
    gens = out["record"]["generations"]
    checks = [c for g in gens for c in g["checks"].values()]
    return {"id": query["id"], "language": query["language"], "kind": query["kind"],
            "retrieval": {"status": dossier["status"], "direct": dossier["direct"],
                          "evidence": [e["locator"].get("path") for e in dossier["evidence"]]},
            "status": v["status"], "reason": v["reason"], "generations": len(gens),
            "problems": [g["problem"] for g in gens if g["problem"]],
            "accepted": [{k: c[k] for k in ("claim_id", "kind", "text_en", "support")} for c in v["claims"]],
            "rejected": v["rejected"], "unresolved": v["uncertainty"],
            "checked": {"claims": len(checks), "rejected": sum(c["state"] == "rejected" for c in checks)},
            "missing": [r["text"] for r in v["missing_requirements"]], "citations": [c["cite"] for c in v["citations"]],
            "text": out["text"], "host": spent,
            # Every drafted claim as checked, rejected ones included: this file is the run's record, not an answer.
            "drafts": [[{**{k: c[k] for k in ("claim_id", "kind", "text_en", "evidence_ids", "source_quotes")},
                         **g["checks"][c["claim_id"]],
                         "answer": ((g["decision"] or {}).get("answers") or {}).get(f"relation_{c['claim_id']}")}
                        for c in (g["draft"] or {}).get("claims", [])] for g in gens],
            "jev_usage": [g["decision"]["usage"] for g in gens if g["decision"]]}


def run(manifest_path: Path, ids: tuple[str, ...], model: str) -> dict:
    manifest = load(manifest_path)
    cfg = decision.config()
    if cfg.mode == "off" or not cfg.key:
        raise SystemExit(f"Jev is not configured: {cfg.status()}")
    active = decision.Config("active", cfg.model, cfg.key_source, key=cfg.key)
    korean: list[str] = []
    real, decision.evaluate = watched(korean)
    kept, knowledge.falls_back = knowledge.falls_back, alone
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix="jev-answers-") as scratch:
            hub, repo = materialize(Path(scratch), manifest["corpus"], None)
            search.HUB = knowledge.HUB = hub
            for query in (q for q in manifest["queries"] if q["id"] in ids):
                dossier = knowledge.prepare(query["text"], repo, cfg=active, cache=None)
                chat = ChatSession(repo, tools="Read,Glob,Grep", system=channels.ANSWER_PROMPT, model=model or None,
                                   isolated=True)
                spent: dict = {}
                try:
                    flow = knowledge.grounded(query["text"], repo, "", dossier, drafting(chat, spent), active,
                                              cache=None)
                    while True:
                        try:
                            next(flow)
                        except StopIteration as stop:
                            out = stop.value
                            break
                finally:
                    chat.close()
                rows.append(summary(query, dossier, out, spent))
    finally:
        decision.evaluate, knowledge.falls_back = real, kept
    usage = [u or {} for r in rows for u in r["jev_usage"]]
    return {"schema": RESULT, "manifest": {"id": manifest["id"], "version": manifest["version"]},
            "run": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **revision()},
            "jev": {"model": cfg.model, "relation_prompt": decision.claims.VERSION,
                    "verification_version": knowledge.VERIFICATION_VERSION},
            "host_model": model or "default", "fallback": ALONE, "statuses": {r["id"]: r["status"] for r in rows},
            "korean_sent": korean,
            "verify_usage": {"requests": len(usage), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                             "output_tokens": sum(u.get("output_tokens", 0) for u in usage)},
            "answers": rows}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/answers.py", description="Live grounded-answer sample")
    parser.add_argument("manifest", type=Path, nargs="?", default=MANIFEST)
    parser.add_argument("--ids", nargs="+", default=list(DEFAULT))
    parser.add_argument("--model", default="")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    result = run(args.manifest, tuple(args.ids), args.model)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RAW / f"answers-{result['manifest']['id']}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "statuses": result["statuses"], "korean_sent": len(result["korean_sent"]),
                      "verify_usage": result["verify_usage"],
                      "answers": [{k: r[k] for k in ("id", "status", "reason", "generations", "rejected", "text")}
                                  for r in result["answers"]]}, ensure_ascii=False, indent=1))
    return 1 if result["korean_sent"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
