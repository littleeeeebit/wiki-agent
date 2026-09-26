"""`python tool/eval/baseline.py <manifest> [--project <repo>] [--k 8] [--out <file>] [--replay <result>]`

The baseline: what retrieval returns today, before Jev or any later stage
changes it, recorded so a change can be compared with it and the record
replayed. Built in this process, with no API call, by the daemon's own
ranking code. `--method hybrid` (the default) is what the daemon serves: BM25
fused with e5 cosine, the model and vectors from the shared user cache, so the
first run may download the model. A run whose vectors do not complete fails
rather than record BM25 under the hybrid name. `--method bm25` needs no model;
the recorded smoke result the tests replay uses it.

A manifest (`eval/jev/*.json`) names its queries and its corpus. A synthetic
corpus is written inline and materialised in a temporary folder; `live` means
this hub and `--project` (default: this repository), whose snapshot — paths and
hashes, never content — then goes only into the result under `raw/eval/jev/`.

`--replay` runs the manifest again and compares with an earlier result: same
manifest, same corpus, same options, same hits. Exit 1 on any difference.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search import HUB, local_index  # noqa: E402
from search.daemon import MODEL_ID  # noqa: E402

METHODS = ("hybrid", "bm25")

MANIFEST = "jev-eval-manifest/1"
RESULT = "jev-eval-result/1"
RAW = HUB / "raw" / "eval" / "jev"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def revision() -> dict:
    def git(*args: str) -> str:
        done = subprocess.run(["git", "-C", str(HUB), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return done.stdout.strip() if done.returncode == 0 else ""

    return {"commit": git("rev-parse", "HEAD") or None, "dirty": bool(git("status", "--porcelain"))}


def load(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST:
        raise ValueError(f"{path}: schema is not {MANIFEST}")
    ids = [q["id"] for q in manifest["queries"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate query ids")
    if manifest["corpus"]["kind"] not in ("synthetic", "live"):
        raise ValueError(f"{path}: corpus kind must be synthetic or live")
    return manifest


def placed(scratch: Path, files: dict[str, str]) -> dict[Path, str]:
    """Where each synthetic page lands, checked for every page before any is
    written. Judged by the resolved location, not by how the name is spelled:
    on Windows `\\` and a drive are separators too, and a spelling check
    missed `repo/..\\..\\x` (review round 3)."""

    roots = [(scratch / "hub").resolve(), (scratch / "repo").resolve()]
    out = {}
    for name, text in files.items():
        target = (scratch / name).resolve()
        if not any(root in target.parents for root in roots):
            raise ValueError(f"{name!r} is outside the corpus")
        out[target] = text
    return out


def run(manifest_path: Path, k: int = 8, project: Path | None = None, method: str = "hybrid") -> dict:
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    manifest = load(manifest_path)
    corpus = manifest["corpus"]
    with tempfile.TemporaryDirectory(prefix="jev-eval-") as scratch:
        if corpus["kind"] == "synthetic":
            hub, repo = Path(scratch) / "hub", Path(scratch) / "repo"
            pages = placed(Path(scratch), corpus["files"])
            (hub / "operator").mkdir(parents=True)
            repo.mkdir()
            for target, text in pages.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Bytes, so Windows does not turn `\n` into `\r\n` and change every hash.
                target.write_bytes(text.encode("utf-8"))
        else:
            hub, repo = HUB, (project or HUB).resolve()

        def label(path: Path) -> str:
            # The repository first: this hub is also a repository, and one
            # name per file keeps a live run's ids stable.
            for name, root in (("repo", repo), ("hub", hub)):
                try:
                    return f"{name}/{path.resolve().relative_to(root.resolve()).as_posix()}"
                except ValueError:
                    continue
            return path.as_posix()

        hybrid = method == "hybrid"
        index = local_index(repo, hub, vectors=hybrid)
        if hybrid and not index.complete():
            raise RuntimeError(f"vectors did not complete (embedder {index.embedder.state}); "
                               "not recording BM25 as hybrid")
        snapshot = sorted((label(p), sha(p.read_bytes())) for p in index.files)
        queries = []
        for query in manifest["queries"]:
            started = time.perf_counter()
            hits = index.search(query["text"], k)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            found = [{"id": label(Path(h["path"])), "line": h["line"], "heading": h["heading"],
                      "bm25": h["bm25"], "cos": h["cos"], "rrf": h["rrf"]} for h in hits]
            expect = query.get("expect") or []
            ids = {h["id"] for h in found}
            queries.append({"id": query["id"], "hits": found, "elapsed_ms": elapsed,
                            "recall": round(sum(e in ids for e in expect) / len(expect), 4) if expect else None})
    scored = [q["recall"] for q in queries if q["recall"] is not None]
    return {
        "schema": RESULT,
        "manifest": {"id": manifest["id"], "version": manifest["version"],
                     "sha256": sha(manifest_path.read_bytes().replace(b"\r\n", b"\n"))},
        "run": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **revision()},
        "corpus": {"kind": corpus["kind"], "files": len(snapshot),
                   "sha256": sha("\n".join(f"{p} {h}" for p, h in snapshot).encode()), "snapshot": snapshot},
        "retrieval": {"method": method, "vectors": hybrid, "k": k, "sources": None},
        # The local embedding model runs on this machine; no request left it,
        # so nothing is priced.
        "models": {"embedding": MODEL_ID if hybrid else None, "jev": None, "answering": None},
        "usage": {"jev_calls": 0, "jev_tokens": 0, "answering_tokens": None, "cost_usd": 0.0},
        "queries": queries,
        "summary": {"queries": len(queries), "scored": len(scored),
                    "mean_recall": round(sum(scored) / len(scored), 4) if scored else None},
    }


def differences(before: dict, after: dict) -> list[str]:
    """What makes two results not the same measurement, or not the same outcome."""

    out = []
    for part, key in (("manifest", "sha256"), ("corpus", "sha256")):
        if before[part][key] != after[part][key]:
            out.append(f"{part} changed: {before[part][key][:12]} -> {after[part][key][:12]}")
    if before["retrieval"] != after["retrieval"]:
        out.append(f"retrieval options changed: {before['retrieval']} -> {after['retrieval']}")
    old = {q["id"]: q["hits"] for q in before["queries"]}
    for query in after["queries"]:
        if old.get(query["id"]) != query["hits"]:
            out.append(f"{query['id']}: hits differ")
    out += [f"{gone}: missing from the replay" for gone in old.keys() - {q["id"] for q in after["queries"]}]
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/baseline.py", description="Record baseline retrieval")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project", type=Path, default=None, help="the repository for a live corpus")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--out", type=Path, default=None, help=f"default: {RAW.relative_to(HUB).as_posix()}/")
    parser.add_argument("--method", choices=METHODS, default="hybrid")
    parser.add_argument("--replay", type=Path, default=None,
                        help="an earlier result to reproduce, with its own k and method")
    args = parser.parse_args(argv)
    if args.replay:
        before = json.loads(args.replay.read_text(encoding="utf-8"))
        result = run(args.manifest, before["retrieval"]["k"], args.project, before["retrieval"]["method"])
        found = differences(before, result)
        print("\n".join(found) or f"replay identical: {len(result['queries'])} queries")
        return 1 if found else 0
    result = run(args.manifest, args.k, args.project, args.method)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RAW / f"{result['manifest']['id']}-{args.method}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    summary = result["summary"]
    print(f"{out}: {summary['queries']} queries, mean recall@{args.k} {summary['mean_recall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
