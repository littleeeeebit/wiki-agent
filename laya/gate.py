"""Reserve the gate test (`docs/plans/laya/2-data.md`, "Gate test") before any measurement.

    python laya/gate.py

Reads `laya/data/pool.jsonl` (real production states only; `pool.py` admits nothing else),
collapses near-duplicates (word 5-shingle Jaccard >= 0.8 on the state text, or on any
passage view of it) into one unit,
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

from kinds import PASSAGE, view
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


def signatures(row: dict) -> list[set]:
    """What a model can be shown of `row`: the whole state, and each passage view
    (`kinds.view`). Two rows are near-duplicates when any of these match."""

    views = {json.dumps(view(n, q["kind"], row["state"]), sort_keys=True)
             for n, q in row["questions"].items() if q["kind"] in PASSAGE}
    return [shingles(row["state"])] + [shingles(json.loads(v)) for v in sorted(views)]


def pairs(rows: list[dict]):
    """`(i, j)` for every two rows with a signature each at Jaccard >= JACCARD. Candidates
    come from an index of shingles, so only signatures that share one are compared."""

    sigs = [(i, s) for i, row in enumerate(rows) for s in signatures(row)]
    index: dict[tuple, list[int]] = {}
    for k, (_, s) in enumerate(sigs):
        for shingle in s:
            index.setdefault(shingle, []).append(k)
    found = set()
    for k, (i, s) in enumerate(sigs):
        shared: dict[int, int] = {}
        for shingle in s:
            for m in index[shingle]:
                if m > k:
                    shared[m] = shared.get(m, 0) + 1
        for m, n in shared.items():
            j = sigs[m][0]
            if j != i and n >= JACCARD * (len(s) + len(sigs[m][1]) - n):
                found.add((min(i, j), max(i, j)))
    return found


def units(rows: list[dict]) -> list[list[dict]]:
    """Rows grouped so near-duplicates (transitively) share a unit."""

    parent = list(range(len(rows)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in pairs(rows):
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


def development(rows: list[dict], gate: set[str]) -> list[dict]:
    """The rows outside the gate test and near-duplicate to none of it, by whole state or
    by passage view. The 2026-10-13 reservation compared whole states only, so a few
    development rows have a view that is a near-duplicate of a gate row's; they are left
    out here rather than the gate being drawn again."""

    return [r for unit in units(rows) if not any(u["state_hash"] in gate for u in unit) for r in unit]


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
