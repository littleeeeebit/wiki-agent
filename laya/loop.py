"""The recursive improvement loop for one kind, after RRSI (plan stages 4 and 5).

RRSI (Regularized Recursive Self-Improvement, google-research/rrsi) improves a harness by
rounds: propose with the whole history in view, screen for leakage, evaluate, and adopt a
candidate only when its gain clears the evaluation's noise; a falsified hypothesis is never
proposed again, a stall turns the search to what it has not tried, components that stop
helping are removed, and held-out data never enters selection. Here the candidate is a
round of data:

    round 0   train on `train`, score `validation`                         -> incumbent
    round r   errors of the incumbent -> the error notebook (`student_wrong`)
              pick the slice with most errors that the notebook does not mark
              falsified or ambiguous, preferring a feature not tried since the last stall
              generate states aimed at it, reading `notes.brief(kind)` first
              label them by agreement (drops -> the notebook)
              - labellers agree on under `MIN_AGREEMENT` -> slice `ambiguous`, next round
              retrain from the base on the incumbent's splits + this round, score validation
              - paired gain over the noise floor -> adopt: new incumbent, slice `resolved`
                when its errors fall under `MIN_ERRORS`, else stays `targeted`
              - otherwise                        -> not adopted, slice `falsified`, stall
    prune     retrain without each adopted round; drop it when removing it costs no more
              than the noise floor

The noise floor is two bootstrap standard deviations of the paired accuracy difference on
validation. Each round retrains from the base checkpoint, so a round not adopted leaves
nothing behind. Validation chooses everything here, so its accuracy is optimistic; the
gate test is never read. Every round is a row of `laya/data/<kind>/rounds.jsonl`.

`generate(kind, slice, n, brief, round)` writes `aug-<round>.candidates.jsonl` rows aimed at
a slice, with each question's `intended` answer, and records `off_slice` and `leak` drops
itself; building them through the production request builders is plan stage 2. The loop
takes it as an argument, as it takes `train`, `score` and `label`.
"""

import json
import random
import re
import time
from collections import Counter

import notes
from label import DATA, append, read

ROUNDS = 3
MIN_ERRORS = 10
MIN_AGREEMENT = 0.5
PER_ROUND = 300
RESAMPLES = 1000
NEGATION = re.compile(r"\b(?:not|no|never|without|none|cannot)\b|n't\b", re.I)


def features(row: dict, name: str) -> dict:
    """What a slice can be cut on, from what the row already carries."""

    text = json.dumps(row["state"], ensure_ascii=False)
    q = row["questions"][name]
    return {"label": str(row["expected"][name]), "options": len(q.get("criteria") or {}) or 2,
            "length": "short" if len(text) < 2000 else "medium" if len(text) < 6000 else "long",
            "numbers": bool(re.search(r"\d", text)), "negation": bool(NEGATION.search(text)),
            "topic": (row.get("provenance") or {}).get("topic", "production")}


def items(rows: list[dict]) -> list[tuple[str, str]]:
    return [(r["provenance"]["state_hash"], n) for r in rows for n in r["expected"]]


def slices(rows: list[dict], wrong: set[tuple[str, str]]) -> list[tuple[tuple[str, object], int, int]]:
    """`(feature, value)`, errors, size: every slice of validation, most errors first.
    `wrong` holds `(state_hash, question)` of each wrong answer."""

    size, errors = Counter(), Counter()
    for row in rows:
        for name in row["expected"]:
            for key in features(row, name).items():
                size[key] += 1
                errors[key] += (row["provenance"]["state_hash"], name) in wrong
    return sorted(((k, errors[k], size[k]) for k in size), key=lambda s: (-s[1], -s[1] / s[2]))


def pick(found, avoid: set, stalled: set) -> tuple | None:
    """The slice to aim at: most errors, never one to avoid, and after a stall a feature
    not tried since (RRSI's exploration of untried components) when one qualifies."""

    ok = [key for key, err, _ in found if err >= MIN_ERRORS and key not in avoid]
    fresh = [key for key in ok if key[0] not in stalled]
    return (fresh or ok or [None])[0]


def floor(universe: list, old: set, new: set, seed: int = 0) -> tuple[float, float]:
    """The paired accuracy gain of `new` over `old` (their wrong sets) on `universe`, and the
    noise floor: two bootstrap standard deviations of that gain."""

    diff = [(x in old) - (x in new) for x in universe]   # +1 fixed, -1 broken, 0 same
    n = len(diff)
    rng = random.Random(seed)
    boots = [sum(diff[rng.randrange(n)] for _ in range(n)) / n for _ in range(RESAMPLES)]
    mean = sum(boots) / RESAMPLES
    sd = (sum((b - mean) ** 2 for b in boots) / (RESAMPLES - 1)) ** 0.5
    return sum(diff) / n, 2 * sd


def note_errors(kind: str, round: int, rows: list[dict], wrong: set, predicted: dict, target=None) -> None:
    """Record the incumbent's validation errors: those inside the slice the round aimed at
    under that slice, so its page shows what is left, the rest under `base`."""

    for row in rows:
        for name, want in row["expected"].items():
            key = (row["provenance"]["state_hash"], name)
            if key in wrong:
                inside = target is not None and features(row, name).get(target[0]) == target[1]
                notes.record(kind, "student_wrong", round=round, slice=target if inside else "base",
                             state_hash=key[0],
                             source=row["provenance"]["source"], question=name, intended=want,
                             predicted=predicted.get(key), state=row["state"])


def run(kind: str, *, train, score, generate, label, rounds: int = ROUNDS, per_round: int = PER_ROUND,
        writer=None, log=print) -> dict:
    """Run the loop for `kind`.

    - `train(kind, splits) -> checkpoint` trains from the base on those splits' labelled files.
    - `score(kind, checkpoint) -> (wrong, predicted)`: the `(state_hash, question)` it gets
      wrong on validation, and its answer for each.
    - `generate(kind, slice, n, brief, round)` and `label(kind, split, target, round, slice)`
      as the module docstring says; `label` returns `label.label`'s stats.
    - `writer` diagnoses notebook pages (`notes.write_pages`); without it pages hold cases.
    """

    folder = DATA / kind
    validation = read(folder / "validation.jsonl")
    universe = items(validation)
    if not universe:
        raise ValueError(f"{kind} has no labelled validation")

    def record(**row):
        append(folder / "rounds.jsonl", {"kind": kind, "at": time.time(), **row})
        log(json.dumps(row, ensure_ascii=False, default=str))

    def evaluate(splits):
        ckpt = train(kind, splits)
        wrong, predicted = score(kind, ckpt)
        return ckpt, set(wrong), predicted

    kept = ["train"]
    best_ckpt, wrong, predicted = evaluate(kept)
    note_errors(kind, 0, validation, wrong, predicted)
    record(round=0, splits=kept, accuracy=1 - len(wrong) / len(universe), checkpoint=str(best_ckpt))
    stalled: set = set()
    for r in range(1, rounds + 1):
        notes.write_pages(kind, writer, log)
        avoid = {tuple(s) for s in notes.brief(kind)["avoid"]}
        target = pick(slices(validation, wrong), avoid, stalled)
        if target is None:
            record(round=r, stopped="no_slice")
            break
        page = notes.cluster_name("student_wrong", target)
        notes.set_status(kind, page, "targeted", r, target)
        split = f"aug-{r}"
        generate(kind, target, per_round, notes.brief(kind), r)
        stats = label(kind, split, per_round, r, target)
        asked = stats["agreed"] + stats["dropped"]
        if not asked or stats["agreed"] / asked < MIN_AGREEMENT:
            notes.set_status(kind, page, "ambiguous", r)
            stalled.add(target[0])
            record(round=r, slice=target, labelled=stats, adopted=False, reason="ambiguous_slice")
            continue
        ckpt, new_wrong, new_predicted = evaluate(kept + [split])
        gain, noise = floor(universe, wrong, new_wrong, seed=r)
        if gain <= noise:
            notes.set_status(kind, page, "falsified", r)
            stalled.add(target[0])
            record(round=r, slice=target, labelled=stats, gain=gain, noise=noise, adopted=False,
                   reason="within_noise" if gain >= 0 else "regressed")
            continue
        kept.append(split)
        best_ckpt, wrong, predicted = ckpt, new_wrong, new_predicted
        note_errors(kind, r, validation, wrong, predicted, target)
        left = next((err for key, err, _ in slices(validation, wrong) if key == target), 0)
        notes.set_status(kind, page, "resolved" if left < MIN_ERRORS else "targeted", r)
        stalled.clear()
        record(round=r, slice=target, labelled=stats, gain=gain, noise=noise, adopted=True,
               accuracy=1 - len(wrong) / len(universe), checkpoint=str(ckpt))
    for split in [s for s in kept if s != "train"]:
        rest = [s for s in kept if s != split]
        ckpt, rest_wrong, rest_predicted = evaluate(rest)
        keeping, noise = floor(universe, rest_wrong, wrong)   # what keeping `split` gains over dropping it
        removed = keeping <= noise
        if removed:
            kept, best_ckpt, wrong, predicted = rest, ckpt, rest_wrong, rest_predicted
        record(prune=split, gain_of_keeping=keeping, noise=noise, removed=removed)
    notes.write_pages(kind, writer, log)
    return {"kind": kind, "accuracy": 1 - len(wrong) / len(universe), "checkpoint": best_ckpt, "splits": kept}
