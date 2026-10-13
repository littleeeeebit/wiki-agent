"""Reserve the gate test (`docs/plans/laya/2-data.md`, "Gate test") before any measurement.

    python laya/gate.py

Reads `laya/data/pool.jsonl` (real production states only; `pool.py` admits nothing else),
collapses near-duplicates (word 5-shingle Jaccard >= 0.8 on the state text) into one unit,
and reserves two thirds of the units at random for the gate test (owner, 2026-10-13: the
pilot kind has 185 real states, and the rest are the development pool). A unit is a state,
not a question: the kinds asked of one state go to the same side. `eval/laya/tests.json`
records hashes, source and locator, never text, and is written once.
"""

import json
import random
import sys
from datetime import date
from pathlib import Path

from store import read

ROOT = Path(__file__).resolve().parents[1]
POOL = Path(__file__).resolve().parent / "data" / "pool.jsonl"
TESTS = ROOT / "eval" / "laya" / "tests.json"
SEED = 20261013
FRACTION = 2 / 3
JACCARD = 0.8


def shingles(state) -> set[tuple[str, ...]]:
    words = json.dumps(state, sort_keys=True, ensure_ascii=False).split()
    return {tuple(words[i:i + 5]) for i in range(max(1, len(words) - 4))}


def units(rows: list[dict]) -> list[list[dict]]:
    """Rows grouped so near-duplicates (transitively) share a unit."""

    sets = [shingles(r["state"]) for r in rows]
    parent = list(range(len(rows)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # ponytail: O(n^2) pairs, fine for a few hundred states; MinHash if the pool reaches thousands
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = sets[i], sets[j]
            if len(a & b) >= JACCARD * len(a | b):
                parent[root(i)] = root(j)
    groups: dict[int, list[dict]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append(row)
    return sorted(groups.values(), key=lambda g: min(r["state_hash"] for r in g))


def record(row: dict) -> dict:
    action = row["decision_kind"] == "action"
    return {"state_hash": row["state_hash"], "source": "raw/specs" if action else "langfuse",
            "locator": row["id"], "kinds": sorted({q["kind"] for q in row["questions"].values()})}


def reserve(rows: list[dict]) -> dict:
    found = units(rows)
    chosen = random.Random(SEED).sample(range(len(found)), round(FRACTION * len(found)))
    tests = [[record(r) for r in found[i]] for i in sorted(chosen)]
    return {"reserved": date.today().isoformat(), "seed": SEED, "fraction": round(FRACTION, 4),
            "jaccard": JACCARD, "pool_states": len(rows), "units": len(found), "tests": tests}


def reserved() -> set[str]:
    """Every state hash in the gate test, near-duplicates included."""

    return {r["state_hash"] for unit in json.loads(TESTS.read_text(encoding="utf-8"))["tests"] for r in unit}


if __name__ == "__main__":
    if TESTS.exists():
        sys.exit(f"{TESTS} exists: the gate test is reserved once")
    result = reserve(read(POOL))
    TESTS.parent.mkdir(parents=True, exist_ok=True)
    TESTS.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for unit in result["tests"]:
        for kind in {k for r in unit for k in r["kinds"]}:
            counts[kind] = counts.get(kind, 0) + 1
    print(f"{len(result['tests'])} of {result['units']} units reserved: {dict(sorted(counts.items()))}")
