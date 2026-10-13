"""Collect the decision requests production really sent, from the local Langfuse.

`tool/main/tracing.py` records each Jev call with the state and questions it sent
(`route-question`, `grade-evidence`, `judge-claims`). This writes them, one row per
distinct state, to `laya/data/pool.jsonl`:

    {"id", "at", "decision_kind", "state", "state_hash", "questions": {name: {kind, question}}}

The pool is what the gate test and the development cases are drawn from (plan stage 2);
nothing here labels or splits it.

    python laya/pool.py
"""

import base64
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from kinds import kind_of

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
NAMES = ("route-question", "grade-evidence", "judge-claims")


def langfuse() -> tuple[str, str]:
    """The local Langfuse URL and its Basic auth, from the repository's `.env`."""

    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LANGFUSE_") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value.strip().strip('"')
    base = env.get("LANGFUSE_BASE_URL", "http://localhost:3001").rstrip("/")
    if urllib.parse.urlparse(base).hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("the pool is read from a local Langfuse only")
    token = base64.b64encode(f"{env['LANGFUSE_PUBLIC_KEY']}:{env['LANGFUSE_SECRET_KEY']}".encode()).decode()
    return base, "Basic " + token


def observations(name: str):
    base, auth = langfuse()
    cursor = None
    while True:
        query = {"limit": 200, "fields": "core,basic,io,metadata", "name": name, **({"cursor": cursor} if cursor else {})}
        req = urllib.request.Request(f"{base}/api/public/v2/observations?{urllib.parse.urlencode(query)}",
                                     headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = json.load(resp)
        yield from page["data"]
        cursor = page["meta"].get("cursor")
        if not cursor or not page["data"]:
            return


def digest(state) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rows(found) -> list[dict]:
    """One row per distinct state, earliest first; a replay, retry or cache copy of a state
    sent before adds nothing. Questions of a kind this table does not know are left out."""

    out: dict[str, dict] = {}
    for o in sorted(found, key=lambda o: o["startTime"]):
        sent = o["input"] if isinstance(o["input"], dict) else json.loads(o["input"])
        dk = (o.get("metadata") or {}).get("decision_kind", "")
        h = digest(sent["state"])
        row = out.setdefault(h, {"id": o["id"], "at": o["startTime"], "decision_kind": dk,
                                 "state": sent["state"], "state_hash": h, "questions": {}})
        for name, q in sent["questions"].items():
            kind = kind_of(name, dk)
            if isinstance(q.get("question"), dict):   # a DecisionRequest's question, around the one sent
                q = q["question"]
            if kind:
                row["questions"].setdefault(name, {"kind": kind, "question": q})
    return [r for r in out.values() if r["questions"]]


def spec_requests():
    """Stage 8's action requests, which specs keep (`raw/specs/<repo>/<spec>.json`) and
    Langfuse does not, shaped like an observation."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("decision_kind") == "action" and "state_en" in node:
                yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    for path in sorted((ROOT / "raw" / "specs").glob("*/*.json")):
        for req in walk(json.loads(path.read_text(encoding="utf-8-sig"))):
            yield {"id": req.get("request_id") or path.stem, "startTime": str(req.get("deadline", "")),
                   "metadata": {"decision_kind": "action"},
                   "input": {"state": req["state_en"], "questions": req["questions"]}}


def main() -> None:
    found = [o for name in NAMES for o in observations(name)] + list(spec_requests())
    pool = rows(found)
    DATA.mkdir(exist_ok=True)
    with (DATA / "pool.jsonl").open("w", encoding="utf-8") as f:
        for row in pool:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    for row in pool:
        for q in row["questions"].values():
            counts[q["kind"]] = counts.get(q["kind"], 0) + 1
    print(f"{len(found)} observations, {len(pool)} distinct states")
    for kind, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {kind:10} {n}")


if __name__ == "__main__":
    main()
