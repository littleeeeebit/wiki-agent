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
              generate states aimed at it, reading the whole history first: every
              notebook page whatever its status, every cluster's size, every round
              label them by agreement (drops -> the notebook)
              - labelling stopped short                -> stop; a rerun resumes it
              - labellers agree on under `MIN_AGREEMENT` -> slice `ambiguous`, next round
              retrain from the base on the incumbent's splits + this round, score validation
              - paired gain over the noise floor -> adopt: new incumbent, slice `resolved`
                when its errors fall under `MIN_ERRORS`, else stays `targeted`
              - otherwise                        -> not adopted, slice `falsified`, stall
    prune     retrain without each adopted round; drop it when removing it costs no more
              than the noise floor

The noise floor is two bootstrap standard deviations of the paired accuracy difference on
validation, resampling states, not questions. Paid turns come from one approved total. Each round retrains from the base checkpoint, so a round not adopted leaves
nothing behind. Validation chooses everything here, so its accuracy is optimistic; the
gate test is never read. Every round is a row of `laya/data/<kind>/rounds.jsonl`.

`generate(kind, slice, n, history, round, max_turns)` writes `aug-<round>.candidates.jsonl` rows aimed at
a slice, with each question's `intended` answer, and records `off_slice` and `leak` drops
itself; building them through the production request builders is plan stage 2. The loop
takes it as an argument, as it takes `train`, `score` and `label`.
"""

import json
import random
import re
import time
from collections import Counter, defaultdict

import notes
from label import DATA
from store import append, read

ROUNDS = 3
MIN_ERRORS = 10
MIN_AGREEMENT = 0.5
MIN_JUDGED = 30
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
    """The paired accuracy gain of `new` over `old` (their wrong sets) on `universe`, the
    `(state_hash, question)` items, and the noise floor: two bootstrap standard deviations
    of that gain. States are resampled whole, since one state's questions move together."""

    per = defaultdict(lambda: [0, 0])   # state -> [fixed - broken, questions]
    for x in universe:
        per[x[0]][0] += (x in old) - (x in new)
        per[x[0]][1] += 1
    states = list(per.values())
    rng = random.Random(seed)
    boots = []
    for _ in range(RESAMPLES):
        drawn = [states[rng.randrange(len(states))] for _ in states]
        boots.append(sum(d for d, _ in drawn) / sum(n for _, n in drawn))
    mean = sum(boots) / RESAMPLES
    sd = (sum((b - mean) ** 2 for b in boots) / (RESAMPLES - 1)) ** 0.5
    return sum(d for d, _ in states) / len(universe), 2 * sd


def note_errors(kind: str, round: int, rows: list[dict], wrong: set, predicted: dict, target=None) -> None:
    """Record the incumbent's validation errors: those inside the slice the round aimed at
    under that slice, so its page shows what is left, the rest under `base`. An error already
    noted for this round is not noted again, so a resumed run can call it safely."""

    noted = {n["id"] for n in notes.ledger(kind)}
    for row in rows:
        for name, want in row["expected"].items():
            key = (row["provenance"]["state_hash"], name)
            if key in wrong and notes.note_id(kind, round, "student_wrong", key[0], name) not in noted:
                inside = target is not None and features(row, name).get(target[0]) == target[1]
                notes.record(kind, "student_wrong", round=round, slice=target if inside else "base",
                             state_hash=key[0],
                             source=row["provenance"]["source"], question=name, intended=want,
                             predicted=predicted.get(key), state=row["state"])


def restore(rows: list[dict]) -> tuple[list[str], set, int, tuple | None, tuple]:
    """The loop's state from its journal, `rounds.jsonl`, so running it again continues it:
    the kept splits, the features stalled since the last adoption, the last round that
    aimed at a slice, that round and slice when it never finished (started, or labelling
    stopped short), and the round and slice that made the incumbent (`(0, None)`: the base)."""

    kept, stalled, last, unfinished, incumbent = ["train"], set(), 0, None, (0, None)
    for row in rows:
        if "prune" in row:
            if row["removed"] and row["prune"] in kept:
                kept.remove(row["prune"])
            continue
        if "slice" not in row:
            continue   # a baseline, a resume, or a round that never started
        last, unfinished = row["round"], None
        if row.get("started") or row.get("stopped") == "labelling_incomplete":
            unfinished = (row["round"], tuple(row["slice"]))
        elif row.get("adopted"):
            kept.append(row.get("split", f"aug-{row['round']}"))   # rows before `split` was kept
            stalled.clear()
            incumbent = (row["round"], tuple(row["slice"]))
        else:
            stalled.add(row["slice"][0])
    return kept, stalled, last, unfinished, incumbent


def run(kind: str, *, train, score, generate, label, max_turns: int, round_turns: int, rounds: int = ROUNDS,
        per_round: int = PER_ROUND, writer=None, log=print) -> dict:
    """Run the loop for `kind`.

    - `train(kind, splits) -> checkpoint` trains from the base on those splits' labelled files.
    - `score(kind, checkpoint) -> (wrong, predicted)`: the `(state_hash, question)` it gets
      wrong on validation, and its answer for each.
    - `generate(kind, slice, n, history, round, max_turns) -> turns used` writes the round's
      candidates as the module docstring says, resuming without paying twice.
    - `label(kind, split, target, round, slice, max_turns)` returns `label.label`'s stats.
    - `writer` diagnoses notebook pages (`notes.write_pages`); without it pages hold cases.
    - `max_turns` is the approved total of paid host turns for the whole run: generation,
      labelling and note writing. `round_turns`, from the pilot, is what one round takes.
      A round starts only when it fits what is left; every callee gets what is left as
      its own limit, and what it used is subtracted.

    Labelling that stops short (the turn limit, or under `MIN_JUDGED` questions judged,
    as when turns fail) stops the loop with the slice still `targeted`: running it again
    resumes, and a shortfall is never read as an ambiguous slice.

    Running it again continues from `rounds.jsonl` (`restore`): the incumbent is retrained
    on the kept splits, an unfinished round resumes with its own slice and split, and a new
    round takes the next number, so a split name never holds two slices. `rounds` caps the
    round number across runs. Every candidate must carry the `slice` it was aimed at.
    """

    folder = DATA / kind
    validation = read(folder / "validation.jsonl")
    universe = items(validation)
    if not universe:
        raise ValueError(f"{kind} has no labelled validation")
    spent = 0

    def record(**row):
        append(folder / "rounds.jsonl", {"kind": kind, "at": time.time(), "spent": spent, **row})
        log(json.dumps(row, ensure_ascii=False, default=str))

    def counted(prompt, payload):
        nonlocal spent
        spent += 1
        return writer(prompt, payload)

    def diagnose(limit):
        notes.write_pages(kind, counted if writer else None, log, limit=max(0, limit))

    def evaluate(splits):
        ckpt = train(kind, splits)
        wrong, predicted = score(kind, ckpt)
        return ckpt, set(wrong), predicted

    def status(r, target, value):
        """Journal first, then the page: a run replays every journalled status on start."""
        notes.set_status(kind, notes.cluster_name("student_wrong", target), value, r, target)

    before = read(folder / "rounds.jsonl")
    for row in before:
        if "status" in row:
            status(row["round"], tuple(row["slice"]), row["status"])
    kept, stalled, last, unfinished, (made, aimed_at) = restore(before)
    best_ckpt, wrong, predicted = evaluate(kept)
    note_errors(kind, made, validation, wrong, predicted, aimed_at)   # what an interruption left out
    record(**({"resumed": True} if before else {"round": 0}), splits=kept,
           accuracy=1 - len(wrong) / len(universe), checkpoint=str(best_ckpt))
    for r in range(unfinished[0] if unfinished else last + 1, rounds + 1):
        if max_turns - spent < round_turns:
            record(round=r, stopped="budget")
            break
        diagnose(max_turns - spent - round_turns)   # notes take only what the round leaves
        history = notes.brief(kind) | {"rounds": read(folder / "rounds.jsonl")}
        target = unfinished[1] if unfinished else \
            pick(slices(validation, wrong), {tuple(s) for s in history["avoid"]}, stalled)
        unfinished = None
        if target is None:
            record(round=r, stopped="no_slice")
            break
        split = f"aug-{r}"
        record(round=r, slice=target, split=split, started=True, status="targeted")   # before any paid turn
        status(r, target, "targeted")
        spent += generate(kind, target, per_round, history, r, max_turns - spent)
        aimed = {json.dumps(c.get("slice")) for c in read(folder / f"{split}.candidates.jsonl")}
        if aimed - {json.dumps(list(target))}:
            raise ValueError(f"{split} holds candidates aimed at {sorted(aimed)}, not {list(target)}")
        stats = label(kind, split, per_round, r, target, max_turns - spent)
        spent += stats["turns"]
        asked = stats["agreed"] + stats["dropped"]
        if stats.get("stopped") == "turn_limit" or asked < MIN_JUDGED:
            record(round=r, slice=target, split=split, labelled=stats, adopted=False,
                   stopped="labelling_incomplete", status="targeted")
            break
        if stats["agreed"] / asked < MIN_AGREEMENT:
            stalled.add(target[0])
            record(round=r, slice=target, split=split, labelled=stats, adopted=False, reason="ambiguous_slice",
                   status="ambiguous")
            status(r, target, "ambiguous")
            continue
        ckpt, new_wrong, new_predicted = evaluate(kept + [split])
        gain, noise = floor(universe, wrong, new_wrong, seed=r)
        if gain <= noise:
            stalled.add(target[0])
            record(round=r, slice=target, split=split, labelled=stats, gain=gain, noise=noise, adopted=False,
                   reason="within_noise" if gain >= 0 else "regressed", status="falsified")
            status(r, target, "falsified")
            continue
        kept.append(split)
        best_ckpt, wrong, predicted = ckpt, new_wrong, new_predicted
        left = next((err for key, err, _ in slices(validation, wrong) if key == target), 0)
        settled = "resolved" if left < MIN_ERRORS else "targeted"
        stalled.clear()
        record(round=r, slice=target, split=split, labelled=stats, gain=gain, noise=noise, adopted=True,
               accuracy=1 - len(wrong) / len(universe), checkpoint=str(ckpt), status=settled)
        note_errors(kind, r, validation, wrong, predicted, target)
        status(r, target, settled)
    for split in [s for s in kept if s != "train"]:
        rest = [s for s in kept if s != split]
        ckpt, rest_wrong, rest_predicted = evaluate(rest)
        keeping, noise = floor(universe, rest_wrong, wrong)   # what keeping `split` gains over dropping it
        removed = keeping <= noise
        if removed:
            kept, best_ckpt, wrong, predicted = rest, ckpt, rest_wrong, rest_predicted
        record(prune=split, gain_of_keeping=keeping, noise=noise, removed=removed)
    diagnose(max_turns - spent)
    return {"kind": kind, "accuracy": 1 - len(wrong) / len(universe), "checkpoint": best_ckpt, "splits": kept,
            "spent": spent}
