"""Train states aimed at a slice a kind's model got wrong (plan stage 5, owner 2026-10-15).

Laya improves a kind by adding data where its model is weakest. `uncovered` serves
`coverage`, which learned to answer yes to everything (train 946 yes, 158 no): from a
labelled train state whose requirement the passages cover, it drops every passage from the
pages the question was written about (`provenance.about`), keeping the rest, so the
requirement most likely goes uncovered. The rows are candidates, not labels: guided
labelling decides each answer as it does any other.

    python laya/augment.py uncovered 400 --split aug1
"""

import argparse
import random
import sys

from pool import digest
from store import append, read

from label import DATA


def uncovered(rows: list[dict], labelled: list[dict], n: int, seed: int = 0) -> list[dict]:
    """At most `n` candidates, one per labelled state that has a covered (true) requirement,
    a passage from an `about` page and one from another page: the state without the `about`
    passages, asking only the questions that were true. Drawn with `seed`."""

    covered = {}
    for r in labelled:
        yes = [q for q, v in r["expected"].items() if v is True]
        if yes:
            covered[r["provenance"]["state_hash"]] = yes
    out = []
    for row in rows:
        yes = covered.get(row["state_hash"])
        about = set(row["provenance"].get("about") or ())
        if not yes or not about:
            continue
        state = row["state"]
        listed = set(state.get("complete_passages") or ())
        drop = {p["id"] for p in state["passages"] if (p.get("heading") or "").split(" > ")[0] in about}
        if not drop & listed or not listed - drop:   # nothing to take away, or nothing left to read
            continue
        cut = {**state, "passages": [p for p in state["passages"] if p["id"] not in drop],
               "complete_passages": [p for p in state["complete_passages"] if p not in drop]}
        h = digest(cut)
        out.append({"id": f"{row['id']}-uncovered", "state": cut, "state_hash": h,
                    "questions": {q: row["questions"][q] for q in yes},
                    "provenance": {**row["provenance"], "derived": "uncovered", "from": row["state_hash"],
                                   "dropped": sorted(drop)}})
    random.Random(seed).shuffle(out)
    return out[:n]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("how", choices=["uncovered"])
    p.add_argument("n", type=int)
    p.add_argument("--split", required=True, help="the new candidates file, laya/data/coverage/<split>.candidates.jsonl")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    out = DATA / "coverage" / f"{a.split}.candidates.jsonl"
    if out.exists():
        sys.exit(f"{out} exists; name a new split")
    rows = uncovered(read(DATA / "coverage" / "train.candidates.jsonl"), read(DATA / "coverage" / "train.jsonl"),
                     a.n, a.seed)
    for row in rows:
        append(out, row)
    print(f"{len(rows)} rows, {sum(len(r['questions']) for r in rows)} questions -> {out}")


if __name__ == "__main__":
    main()
