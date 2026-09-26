"""`python tool/eval/graph.py [<manifest>] --live [--model M] [--out <file>]`

Stage 4's extraction check: each labeled passage (`eval/jev/extraction.json`)
goes through the extraction a repository's passages go through — the model's
proposal (`main.knowledge.propose`), code's span checks
(`search.knowledge_graph.validate`) and Jev's support
(`main.knowledge.supported`) — and is held against labels a person wrote
before any run. Entities, proposed dependencies and adopted dependencies are
scored apart, precision and recall each, so a bigger graph cannot pass for a
better one.

`--live` is required: the passages go to the chat's CLI and to Jev on the
TypeSafe key, and a Korean one to Gemini. The result goes to `raw/eval/jev/`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from main import knowledge  # noqa: E402
from search import knowledge_graph  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "eval" / "jev" / "extraction.json"
RAW = ROOT / "raw" / "eval" / "jev"


def score(found: set, expected: set) -> dict:
    hit = len(found & expected)
    return {"found": len(found), "expected": len(expected), "correct": hit,
            "precision": round(hit / len(found), 3) if found else None,
            "recall": round(hit / len(expected), 3) if expected else None}


def run(manifest: Path, cfg: decision.Config, model: str = "", proposer=None) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != "jev-graph-manifest/1":
        raise ValueError(f"{manifest}: not a jev-graph-manifest/1")
    passages = data["passages"]
    chunks = [{"chunk_id": knowledge_graph.digest("eval", p["id"]), "source_id": knowledge_graph.digest("eval", p["id"]),
               "heading": p["heading"], "text": p["text"], "visibility": "repository"} for p in passages]
    proposals, used = (proposer or knowledge.propose)(
        [{"id": str(i), "heading": c["heading"], "text": c["text"]} for i, c in enumerate(chunks)], model)
    items, trace = [], []
    for i, chunk in enumerate(chunks):
        entities, relations, rejected = knowledge_graph.validate(chunk["text"], proposals.get(str(i)) or {})
        items.append((chunk, {"entities": entities, "relations": relations, "rejected": rejected}))
    budget = Budget(seconds=knowledge.EXTRACTION["seconds"], calls=4, candidates=0)
    knowledge.supported(items, cfg, budget, trace, None)
    norm = knowledge_graph.normal
    sets = {name: (set(), set()) for name in ("entities", "typed_entities", "proposed", "adopted")}
    wrong_adopted, rows = [], []
    for passage, (_chunk, result) in zip(passages, items):
        pid = passage["id"]
        expected_entities = {(pid, norm(e["name"])) for e in passage["entities"]}
        expected_relations = {(pid, norm(r["from"]), norm(r["to"])) for r in passage["relations"]}
        found_entities = {(pid, norm(e["name"])) for e in result["entities"]}
        proposed = {(pid, norm(r["from"]), norm(r["to"])) for r in result["relations"]}
        adopted = {(pid, norm(r["from"]), norm(r["to"])) for r in result["relations"]
                   if r["support"] is not None and r["support"] >= knowledge_graph.ADOPT}
        types = {norm(e["name"]): e["type"] for e in passage["entities"]}
        for key, found, expected in (("entities", found_entities, expected_entities), ("proposed", proposed, expected_relations),
                                     ("adopted", adopted, expected_relations)):
            sets[key][0].update(found)
            sets[key][1].update(expected)
        sets["typed_entities"][0].update((pid, norm(e["name"]), e["type"]) for e in result["entities"])
        sets["typed_entities"][1].update((pid, n, t) for n, t in types.items())
        negatives = {(pid, norm(r["from"]), norm(r["to"])) for r in passage["not_relations"]}
        wrong_adopted += sorted(adopted & negatives)
        rows.append({"id": pid, "entities": result["entities"], "relations": result["relations"],
                     "rejected": result["rejected"], "missed_entities": sorted(e for _p, e in expected_entities - found_entities),
                     "extra_entities": sorted(e for _p, e in found_entities - expected_entities)})
    return {"schema": "jev-graph-result/1", "manifest": {"id": data["id"], "version": data["version"]},
            "run": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": used,
            "jev": cfg.status(), "passages": rows, "trace": trace, "budget": budget.record(),
            "summary": {**{name: score(*pair) for name, pair in sets.items()},
                        "negatives_adopted": [list(x) for x in wrong_adopted]}}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/graph.py", description="Extraction precision and recall")
    parser.add_argument("manifest", type=Path, nargs="?", default=MANIFEST)
    parser.add_argument("--live", action="store_true", help="send the passages to the model and to Jev")
    parser.add_argument("--model", default="")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("the passages are sent to the chat's CLI and to Jev; pass --live to send them")
    result = run(args.manifest, decision.config(), args.model)
    out = args.out or RAW / f"graph-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
    print(out)
    return 0 if not result["summary"]["negatives_adopted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
