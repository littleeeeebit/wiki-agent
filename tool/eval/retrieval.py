"""`python tool/eval/retrieval.py [<manifest>] [--project <repo>] [--k 8] [--method bm25|hybrid] [--out <file>]`

Stage 5's comparison: every query of a manifest (`eval/jev/bridge.json` by
default) retrieved twice from one index by `search.retrieval` — the graph
lane off, then on under the manifest's `graph` budget — with no API call.

Recorded per query: the hits of each arm, the recall of the seeds and of
everything returned, whether the seeds are the same in both arms (the walk
may add candidates, never displace one), and every path the walk took —
reached, corroborating, or refused and why. A query whose expected passage
the walk did not reach stays in the record as a missed bridge; nothing is
dropped to make the numbers look better. Recall is at the level each
expectation names: `file#Heading` one section, a bare file any of its.

Stage 10 owns the acceptance thresholds; this only measures. `bm25` (the
default) needs no model; `hybrid` loads e5 as `baseline.py` does and fails
rather than record BM25 under its name.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import baseline  # noqa: E402
from search import evidence, local_index, retrieval  # noqa: E402

MANIFEST = baseline.HUB / "eval" / "jev" / "bridge.json"
RESULT = "jev-retrieval-eval/1"
SOURCES = ["hub", "documents", "memory"]


def recall(expect: list[str], hits: list[str]) -> float | None:
    if not expect:
        return None
    files = {h.split("#")[0] for h in hits}
    return round(sum((e in hits) if "#" in e else (e in files) for e in expect) / len(expect), 4)


def run(manifest_path: Path = MANIFEST, k: int = 8, project: Path | None = None, method: str = "bm25") -> dict:
    if method not in baseline.METHODS:
        raise ValueError(f"method must be one of {baseline.METHODS}")
    manifest = baseline.load(manifest_path)
    budget = manifest.get("graph") or retrieval.GRAPH
    allowance = manifest.get("max_candidates") or retrieval.MAX_CANDIDATES
    with tempfile.TemporaryDirectory(prefix="jev-eval-") as scratch:
        hub, repo = baseline.materialize(Path(scratch), manifest["corpus"], project)
        hybrid = method == "hybrid"
        index = local_index(repo, hub, vectors=hybrid)
        try:
            if hybrid and not index.complete():
                raise RuntimeError(f"vectors did not complete (embedder {index.embedder.state})")

            def label(path: Path) -> str:
                for name, root in (("repo", repo), ("hub", hub)):
                    try:
                        return f"{name}/{path.resolve().relative_to(root.resolve()).as_posix()}"
                    except ValueError:
                        continue
                return path.as_posix()

            names = {c["chunk_id"]: f"{label(Path(c['path']))}#{c['heading_path'][-1]}" for c in index.chunks}
            snapshot = sorted((label(p), baseline.sha(p.read_bytes())) for p in index.files)
            queries = [one(index, query, k, budget, allowance, names) for query in manifest["queries"]]
        finally:
            index.close()
    scored = [q for q in queries if q["expect"]]

    def mean(values: list) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "schema": RESULT,
        "manifest": {"id": manifest["id"], "version": manifest["version"],
                     "sha256": baseline.sha(manifest_path.read_bytes().replace(b"\r\n", b"\n"))},
        "run": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **baseline.revision()},
        "corpus": {"kind": manifest["corpus"]["kind"], "files": len(snapshot),
                   "sha256": baseline.sha("\n".join(f"{p} {h}" for p, h in snapshot).encode()), "snapshot": snapshot},
        "retrieval": {"method": method, "k": k, "sources": SOURCES, "graph": budget, "max_candidates": allowance,
                      "schema": retrieval.RESULT},
        "usage": {"jev_calls": 0, "jev_tokens": 0, "cost_usd": 0.0},
        "queries": queries,
        "summary": {"queries": len(queries), "scored": len(scored),
                    "recall_graph_off": mean([q["off"]["recall"] for q in scored]),
                    "recall_graph_on": mean([q["on"]["recall"] for q in scored]),
                    "seed_recall_graph_on": mean([q["on"]["seed_recall"] for q in scored]),
                    "seeds_identical": sum(q["seeds_identical"] for q in queries),
                    "gained": [q["id"] for q in scored if q["on"]["recall"] > q["off"]["recall"]],
                    "missed_bridges": [q["id"] for q in scored if q["on"]["recall"] < 1],
                    "paths": dict(Counter(p["status"] for q in queries for p in q["paths"]))},
    }


def one(index, query: dict, k: int, budget: dict, allowance: int, names: dict[str, str]) -> dict:
    arms = {}
    for arm, graph in (("off", None), ("on", budget)):
        req = retrieval.request(evidence.repo_id(index.project), query["text"], sources=SOURCES, limit=k,
                                seconds=60.0, graph=graph, max_candidates=allowance)
        arms[arm] = retrieval.run(index.snapshot(), req)
    expect = query.get("expect") or []

    def hits(result: dict, lanes: tuple[str, ...]) -> list[str]:
        return [names[c["chunk_id"]] for c in result["chunks"] if c["lane"] in lanes]

    off, on = arms["off"], arms["on"]
    return {"id": query["id"], "kind": query.get("kind"), "expect": expect,
            "off": {"hits": hits(off, ("rrf",)), "recall": recall(expect, hits(off, ("rrf",))),
                    "elapsed_ms": off["elapsed_ms"]},
            "on": {"seeds": hits(on, ("rrf",)), "graph": hits(on, ("graph",)),
                   "seed_recall": recall(expect, hits(on, ("rrf",))),
                   "recall": recall(expect, hits(on, ("rrf", "graph"))),
                   "truncated": on["truncated"], "elapsed_ms": on["elapsed_ms"]},
            "seeds_identical": hits(on, ("rrf",)) == hits(off, ("rrf",)),
            "paths": [{"to": names.get(p["to"], p["to"]), "seed": names.get(p["seed"], p["seed"]),
                       "status": p["status"], "hops": p["hops"],
                       "edges": [s["kind"] + (" (reverse)" if s["reverse"] else "") for s in p["steps"][1:]]}
                      for p in on["paths"]]}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/retrieval.py", description="Graph lane off and on")
    parser.add_argument("manifest", type=Path, nargs="?", default=MANIFEST)
    parser.add_argument("--project", type=Path, default=None, help="the repository for a live corpus")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--method", choices=baseline.METHODS, default="bm25")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"default: {baseline.RAW.relative_to(baseline.HUB).as_posix()}/")
    args = parser.parse_args(argv)
    result = run(args.manifest, args.k, args.project, args.method)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or baseline.RAW / f"{result['manifest']['id']}-retrieval-{args.method}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    s = result["summary"]
    print(f"{out}: recall@{args.k} graph off {s['recall_graph_off']} -> on {s['recall_graph_on']}; "
          f"seeds identical {s['seeds_identical']}/{s['queries']}; gained {s['gained']}; "
          f"missed {s['missed_bridges']}; paths {s['paths']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
