"""Stage 6's gate for one kind (`docs/plans/laya/6-rollout.md`): Laya against Jev on the
reserved gate states, read here for the first time.

    laya/.venv/Scripts/python laya/jev_gate.py coverage --support <checkpoint dir> --joint <checkpoint dir>

The labels are guided labelling's on the gate states (`coverage/gate.jsonl`, from
`gate.candidates.jsonl`), which never see either decider's answer. Jev's side is the answer
production recorded in the local Langfuse for the same state and question (`jev_answers`),
not a paid run (owner, 2026-10-19). Laya's coverage answer is support's, and where support
left a question undecided, joint's over its part picks (`joint.evaluate`).

One test state is one reserved logical unit (`units`): of a unit's labelled hashes, the
first is evaluated. An answer is right when its verdict matches the label; no answer
counts as wrong. The gate passes when the lower bound of the paired 95% interval of Laya's
accuracy minus Jev's, resampling units, is at least -`MARGIN`, and Laya's ECE is no worse
than Jev's by more than `ECE_MARGIN`. Under `MIN_STATES` units, or units both answered,
it is inconclusive. Beside the verdict, both deciders' accuracy per Jev weak spot (`WEAK`).
Results go to `raw/eval/laya/gate-<kind>-<timestamp>.json`.
"""

import argparse
import json
import random
import re
import time

import gate
import joint
import pool
import stage1
import support
from label import DATA
from store import read

POLICY = stage1.ROOT / "eval" / "jev" / "policy.json"
MARGIN, ECE_MARGIN, MIN_STATES = 0.05, 0.05, 100
RESAMPLES, SEED, BINS = 10_000, 20261019, 10


def right(verdict, expected) -> bool:
    return verdict is not None and verdict == expected


def confidence(verdict, p_yes) -> float:
    """The probability a decider gave its own verdict."""

    return p_yes if verdict else 1 - p_yes


def ece(pairs: list[tuple[float, bool]], bins: int = BINS) -> float:
    """Expected calibration error of `(confidence, right)` pairs over equal-width bins."""

    out = 0.0
    for b in range(bins):
        inside = [(c, ok) for c, ok in pairs if min(int(c * bins), bins - 1) == b]
        if inside:
            out += abs(sum(c for c, _ in inside) - sum(ok for _, ok in inside))
    return out / max(1, len(pairs))


def units() -> dict[str, tuple[int, int]]:
    """Each reserved state hash's logical unit and place in it (`eval/laya/tests.json`):
    replays and near-duplicates are one test state."""

    tests = json.loads(gate.TESTS.read_text(encoding="utf-8"))["tests"]
    return {r["state_hash"]: (i, j) for i, unit in enumerate(tests) for j, r in enumerate(unit)}


def one_per_unit(rows: list[dict], place: dict) -> list[dict]:
    """The first labelled row of each logical unit, in the reservation's order: a unit is
    evaluated, counted and resampled once (PR #181 review: 114 hashes were 94 units)."""

    out = {}
    for row in sorted(rows, key=lambda r: place[r["provenance"]["state_hash"]]):
        out.setdefault(place[row["provenance"]["state_hash"]][0], row)
    return list(out.values())


def paired(records: list[dict], resamples: int = RESAMPLES, seed: int = SEED) -> tuple[float, float, float]:
    """Laya's accuracy minus Jev's over `records`, and its 95% interval when gate units,
    with all their questions, are resampled."""

    by_state: dict = {}
    for r in records:
        by_state.setdefault(r["unit"], []).append(
            right(r["laya_verdict"], r["expected"]) - right(r["jev_verdict"], r["expected"]))
    states = list(by_state.values())

    def diff(drawn):
        return sum(map(sum, drawn)) / max(1, sum(map(len, drawn)))

    rng = random.Random(seed)
    boot = sorted(diff([rng.choice(states) for _ in states]) for _ in range(resamples))
    return diff(states), boot[round(0.025 * (resamples - 1))], boot[round(0.975 * (resamples - 1))]


def verdict(records: list[dict]) -> dict:
    """The gate's summary and verdict over one record per labelled question."""

    answered = [r for r in records if r["laya_verdict"] is not None and r["jev_verdict"] is not None]
    states, answered_states = {r["unit"] for r in records}, {r["unit"] for r in answered}
    diff, lo, hi = paired(records)
    ece_laya, ece_jev = (ece([(confidence(r[f"{d}_verdict"], r[d]), right(r[f"{d}_verdict"], r["expected"]))
                              for r in answered]) for d in ("laya", "jev"))
    if len(states) < MIN_STATES or len(answered_states) < MIN_STATES:
        said = "inconclusive"
    else:
        said = "pass" if lo >= -MARGIN and ece_laya - ece_jev <= ECE_MARGIN else "fail"
    return {"states": len(states), "questions": len(records), "answered_states": len(answered_states),
            **{f"{d}_accuracy": round(sum(right(r[f"{d}_verdict"], r["expected"]) for r in records)
                                      / max(1, len(records)), 4) for d in ("laya", "jev")},
            **{f"{d}_answered": round(sum(r[f"{d}_verdict"] is not None for r in records) / max(1, len(records)), 4)
               for d in ("laya", "jev")},
            # a gate mostly of one class: accuracy alone is near always answering it
            **{f"{d}_{c}": f"{sum(right(r[f'{d}_verdict'], r['expected']) for r in records if r['expected'] is y)}"
                           f"/{sum(r['expected'] is y for r in records)}"
               for d in ("laya", "jev") for c, y in (("yes_right", True), ("no_right", False))},
            "difference": round(diff, 4), "interval": [round(lo, 4), round(hi, 4)],
            "ece_laya": round(ece_laya, 4), "ece_jev": round(ece_jev, 4), "verdict": said}


# Jev's documented weak spots (https://docs.typesafe.ai/model-jaggedness/jev-1.13), which Laya's
# data must cover (owner, 2026-10-19), as rough tags on a coverage question; reported, not gated.
WEAK = {"numbers": re.compile(r"\d"),
        "dates": re.compile(r"\b(19|20)\d\d\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|"
                            r"\b(yesterday|today|tomorrow|ago|before|after|since|until|deadline)\b", re.I),
        "literal": re.compile(r"\b(not|no|never|without|except|only|unless|neither|nor)\b|n't\b", re.I),
        "injected": re.compile(r"ignore (all |the )?(previous|above)|you must (answer|say)|system prompt", re.I)}


def weak(row: dict, name: str, large: int) -> list[str]:
    """The weak-spot tags of `row`'s question `name`: the requirement's wording, injected
    instructions in its passages, and a state longer than `large` characters."""

    rid = name.split("_", 1)[1]
    text = next(r["text"] for r in row["state"]["requirements"] if r["id"] == rid)
    passages = " ".join(p.get("text", "") for p in row["state"]["passages"])
    tags = [t for t in ("numbers", "dates", "literal") if WEAK[t].search(text)]
    tags += ["injected"] * bool(WEAK["injected"].search(passages))
    return tags + ["large"] * (len(json.dumps(row["state"])) > large)


def by_weakness(records: list[dict], rows: list[dict]) -> dict:
    """Both deciders' accuracy per weak-spot tag; `large` is the longest quarter of states."""

    sizes = sorted(len(json.dumps(r["state"])) for r in rows)
    large = sizes[(3 * len(sizes)) // 4] if sizes else 0
    tags = {(r["provenance"]["state_hash"], n): weak(r, n, large) for r in rows for n in r["expected"]}
    out = {}
    for t in [*WEAK, "large"]:
        got = [r for r in records if t in tags[(r["state_hash"], r["question"])]]
        out[t] = {"questions": len(got), **{f"{d}_accuracy": round(sum(right(r[f"{d}_verdict"], r["expected"])
                                                                     for r in got) / len(got), 4) if got else None
                                           for d in ("laya", "jev")}}
    return out


def under_policy(records: list[dict], rule: dict) -> dict:
    """Jev as production reads it (`eval/jev/policy.json`): yes at or above `rule["yes"]`, no
    at or below `rule["no"]`, uncertain between, which takes the safe path."""

    said = [(r, True if r["jev"] >= rule["yes"] else False if r["jev"] <= rule["no"] else None)
            for r in records if r["jev"] is not None]
    decided = [(r, v) for r, v in said if v is not None]
    return {"rule": rule, "decided": round(len(decided) / max(1, len(said)), 4),
            "accuracy_decided": round(sum(v == r["expected"] for r, v in decided) / max(1, len(decided)), 4)}


def jev_answers(rows: list[dict]) -> dict:
    """Jev's recorded probability of yes for the rows' questions, keyed `(state_hash, name)`:
    from the earliest observation that sent the same state and the same question. A pool row
    merges several observations' questions under the first one's id, so reading that id
    alone misses a question a later observation added (PR #181 review)."""

    want = {(r["provenance"]["state_hash"], n): r["questions"][n] for r in rows for n in r["expected"]}
    out = {}
    seen = sorted((o for traced in pool.NAMES for o in pool.observations(traced)), key=lambda o: o["startTime"])
    for o in seen:
        sent = o["input"] if isinstance(o["input"], dict) else json.loads(o["input"])
        said = o["output"] if isinstance(o["output"], dict) else json.loads(o["output"] or "{}")
        h = pool.digest(sent["state"])
        for name, q in sent["questions"].items():
            q = q["question"] if isinstance(q.get("question"), dict) else q
            if (h, name) in want and (h, name) not in out and q == want[(h, name)] and name in said:
                value = said[name]
                out[(h, name)] = value["choice"] if isinstance(value, dict) else value
    return out


def coverage(support_dir: str, joint_dir: str) -> dict:
    """Laya's `(verdict, probability of yes)` for each labelled gate coverage question."""

    _, records = support.evaluate(support_dir, split="gate")
    chosen = joint.picks(records, read(DATA / "coverage" / "gate.jsonl"))
    _, records = joint.evaluate(joint_dir, split="gate", chosen=chosen)
    return {(r["state_hash"], r["question"]): (r["verdict"], r["score"]) for r in records}


def records(rows: list[dict], laya: dict, jev: dict, place: dict) -> list[dict]:
    out = []
    for row in rows:
        h = row["provenance"]["state_hash"]
        for name, expected in row["expected"].items():
            lv, lp = laya.get((h, name), (None, None))
            jp = jev.get((h, name))
            out.append({"state_hash": h, "unit": place[h][0], "question": name, "expected": expected,
                        "laya_verdict": lv, "laya": lp,
                        "jev_verdict": None if jp is None else jp >= 0.5, "jev": jp})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind", choices=["coverage"])
    p.add_argument("--support", required=True, help="support checkpoint folder")
    p.add_argument("--joint", required=True, help="joint checkpoint folder")
    a = p.parse_args()
    place = units()
    labelled = read(DATA / a.kind / "gate.jsonl")
    rows = one_per_unit(labelled, place)
    asked = sum(len(r["questions"]) for r in read(DATA / a.kind / "gate.candidates.jsonl"))
    got = records(rows, coverage(a.support, a.joint), jev_answers(rows), place)
    out = {"kind": a.kind, "support": a.support, "joint": a.joint, "candidate_questions": asked,
           "labelled_states": len(labelled),
           "labels": "guided labelling (haiku, codex:gpt-6.1-sol), agreed only", "jev": "recorded in Langfuse",
           **verdict(got), "jev_policy": under_policy(got, json.loads(POLICY.read_text("utf-8"))["rules"][a.kind]),
           "weak_spots": by_weakness(got, rows), "records": got}
    stage1.OUT.mkdir(parents=True, exist_ok=True)
    path = stage1.OUT / f"gate-{a.kind}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "records"}, indent=1), "\n->", path)


if __name__ == "__main__":
    main()
