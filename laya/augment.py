"""Train states aimed at a slice a kind's model got wrong (plan stage 5, owner 2026-10-15).

Laya improves a kind by adding data where its model is weakest. Both ways serve `coverage`,
whose weak answer is "no": from a labelled train state whose requirement the passages cover,
they drop passages so the requirement most likely goes uncovered, and ask only the questions
that were true. The rows are candidates, not labels: guided labelling decides each answer
as it does any other.

- `uncovered` drops every passage from the pages the question was written about
  (`provenance.about`). Other pages often cover the requirement too: its first 243 labels
  were 23% no.
- `unsupported` drops every listed passage holding a quote a verifier gave as the evidence
  for a yes (`quotes`, from the guided labelling turns of the train splits named, each with
  its input rows kept as `<split>.candidates.jsonl`), and takes
  only the question types named, the ones validation got most wrong.

    python laya/augment.py uncovered 400 --split aug1
    python laya/augment.py unsupported 400 --split aug2 --types bridging partial unanswered \
        --quotes-from full1 full2 full3 aug1
"""

import argparse
import random
import re
import sys

import gate
from pool import digest
from store import append, read

from label import DATA

QUOTE = 20   # a shorter quote matches passages it was not taken from


def fold(text: str) -> str:
    return " ".join(text.split())


def quotes(turns: list[dict]) -> set[str]:
    """Every passage text a verifier quoted as the evidence for a yes, cut at ellipses, at
    least `QUOTE` characters, whitespace folded."""

    out = set()
    for t in turns:
        verdicts = (t.get("json") or {}).get("verdicts")
        for v in verdicts.values() if isinstance(verdicts, dict) else ():
            if isinstance(v, dict) and str(v.get("answer")).lower() == "yes":
                for part in re.split(r"\.\.\.|…", str(v.get("evidence") or "")):
                    part = fold(part).strip(" \"'“”")
                    if len(part) >= QUOTE:
                        out.add(part)
    return out


def cuts(rows: list[dict], labelled: list[dict], n: int, seed: int, held, how: str, dropping) -> list[dict]:
    """At most `n` candidates, one per labelled state that has a covered (true) requirement
    and passages `dropping(row)` names among its listed ones, with some listed passage left:
    the state without them, asking only the questions that were true. Drawn with `seed`. A cut
    state that is a near-duplicate of a `held` row (the gate, validation, calibration) or of
    one already drawn is left out: dropping passages can bring a train state close to one its
    source was far from."""

    near = gate.Near(held)
    covered = {}
    for r in labelled:
        yes = [q for q, v in r["expected"].items() if v is True]
        if yes:
            covered[r["provenance"]["state_hash"]] = yes
    out = []
    for row in rows:
        yes = covered.get(row["state_hash"])
        if not yes:
            continue
        state = row["state"]
        listed = set(state.get("complete_passages") or ())
        drop = dropping(row)
        if not drop & listed or not listed - drop:   # nothing to take away, or nothing left to read
            continue
        cut = {**state, "passages": [p for p in state["passages"] if p["id"] not in drop],
               "complete_passages": [p for p in state["complete_passages"] if p not in drop]}
        out.append({"id": f"{row['id']}-{how}", "state": cut, "state_hash": digest(cut),
                    "questions": {q: row["questions"][q] for q in yes},
                    "provenance": {**row["provenance"], "derived": how, "from": row["state_hash"],
                                   "dropped": sorted(drop)}})
    random.Random(seed).shuffle(out)
    picked = []
    for r in out:
        if len(picked) == n:
            break
        if not near.found(r):   # two sources that differ only in the dropped pages cut to one state
            picked.append(r)
            near.add(r)
    return picked


def uncovered(rows: list[dict], labelled: list[dict], n: int, seed: int = 0, held=()) -> list[dict]:
    """`cuts` dropping the passages of the `about` pages."""

    def dropping(row):
        about = set(row["provenance"].get("about") or ())
        return {p["id"] for p in row["state"]["passages"] if (p.get("heading") or "").split(" > ")[0] in about}
    return cuts(rows, labelled, n, seed, held, "uncovered", dropping)


def unsupported(rows: list[dict], labelled: list[dict], quoted: set[str], types, n: int, seed: int = 0,
                held=()) -> list[dict]:
    """`cuts` of the states of question `types`, dropping the passages that hold a `quoted` text."""

    def dropping(row):
        if row["provenance"].get("type") not in types:
            return set()
        return {p["id"] for p in row["state"]["passages"] if any(q in fold(p.get("text") or "") for q in quoted)}
    return cuts(rows, labelled, n, seed, held, "unsupported", dropping)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("how", choices=["uncovered", "unsupported"])
    p.add_argument("n", type=int)
    p.add_argument("--split", required=True, help="the new candidates file, laya/data/coverage/<split>.candidates.jsonl")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--types", nargs="+", help="unsupported: the question types to take")
    p.add_argument("--quotes-from", nargs="+", metavar="SPLIT",
                   help="unsupported: the train splits whose verifier turns give the quotes")
    a = p.parse_args()
    if not (a.how == "unsupported") == bool(a.types) == bool(a.quotes_from):
        sys.exit("--types and --quotes-from go with unsupported, and only with it")
    folder = DATA / "coverage"
    out = folder / f"{a.split}.candidates.jsonl"
    if out.exists():
        sys.exit(f"{out} exists; name a new split")
    reserved = gate.reserved()
    gated = [r for r in read(gate.POOL) if r["state_hash"] in reserved]
    if reserved - {r["state_hash"] for r in gated}:
        sys.exit(f"gate states are missing from {gate.POOL.name}; run laya/pool.py first")
    held = gated + [r for s in ("validation", "calibration") for r in read(folder / f"{s}.candidates.jsonl")]
    rows, labelled = read(folder / "train.candidates.jsonl"), read(folder / "train.jsonl")
    if a.how == "uncovered":
        drawn = uncovered(rows, labelled, a.n, a.seed, held)
    else:
        # Calibration sets temperatures and thresholds only, and validation picks checkpoints:
        # a quote from their turns would choose and shape train input. A split's turns judged
        # every state of its input rows, kept labels or not, so the input file must hold none of
        # them. ponytail: `<split>.candidates.jsonl` is taken as the `--rows` file it was labelled
        # from, checked only to contain its labels; guided could record the rows' hash instead.
        kept = {r["state_hash"] for r in held}
        for s in a.quotes_from:
            for name in ("turns", "candidates"):
                if not (folder / f"{s}.{name}.jsonl").exists():
                    sys.exit(f"{s}.{name}.jsonl is not in {folder}: quote splits whose input rows are kept")
            inputs = {r["state_hash"] for r in read(folder / f"{s}.candidates.jsonl")}
            if inputs & kept:
                sys.exit(f"{s} was asked about gate, validation or calibration states; quote train splits only")
            if {r["provenance"]["state_hash"] for r in read(folder / f"{s}.jsonl")} - inputs:
                sys.exit(f"{s} labelled states its candidates file does not hold; it is not that split's input")
        quoted = quotes([t for s in a.quotes_from for t in read(folder / f"{s}.turns.jsonl")])
        drawn = unsupported(rows, labelled, quoted, set(a.types), a.n, a.seed, held)
    for row in drawn:
        append(out, row)
    print(f"{len(drawn)} rows, {sum(len(r['questions']) for r in drawn)} questions -> {out}")


if __name__ == "__main__":
    main()
