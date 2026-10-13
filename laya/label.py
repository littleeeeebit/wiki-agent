"""Two-labeller agreement labels (plan stage 3, without the human queue).

Each candidate state goes to two labellers from different model families, as host turns
(`agent.chat_session.oneshot`: Claude or Codex, no tools, an empty folder). A question is
labelled when both give the same answer with confidence at least `AGREE`; any other
question is dropped, and the next candidate fills its place. Labelling a split stops when
it holds `target` labelled questions, when the candidates run out, or at the turn limit.

Files, per kind, under `laya/data/<kind>/`:

- `<split>.candidates.jsonl` — rows of `pool.rows`' shape, the kind's questions only.
- `<split>.ledger.jsonl` — every labeller turn, kept so a rerun never pays twice.
- `<split>.judged.jsonl` — every judged state's verdict: its labelled row and its dropped
  questions' notes, written before either, so a rerun finishes what an interruption cut.
- `<split>.jsonl` — the labelled rows, in Laya's `{state, questions, expected}` format.

    python laya/label.py useful train --target 3000 --labellers opus codex:gpt-6.1-sol --max-turns 400

Nothing is sent without `--labellers`; `--dry` shows what the first turn would send.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import notes

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
PROMPT = Path(__file__).resolve().parent / "prompts" / "label.md"
AGREE = 0.7


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def shown(question: dict) -> dict:
    """A production question as the labeller reads it: the fallback prompt's shape."""

    return {"type": question["type"], "question": question["instructions"],
            **({"options": question["criteria"]} if "criteria" in question else {})}


def parsed(text: str) -> dict:
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    got = json.loads(body[body.find("{"):]) if "{" in body else {}
    return got if isinstance(got, dict) else {}


def host_json(prompt: Path, payload: dict, model: str) -> dict:
    """One host turn (`agent.chat_session.oneshot`: no tools, an empty folder) answering
    one JSON object: `{"json", "model", "cost_usd"}`, or with `error` and an empty `json`.
    Never raises."""

    if str(ROOT / "tool") not in sys.path:
        sys.path.append(str(ROOT / "tool"))   # after this folder, so `notes` and `label` stay ours
    from agent.chat_session import oneshot

    out = {"model": model, "cost_usd": None, "json": {}}
    # `oneshot` reads its prompt under tool/prompts; an absolute path replaces that folder (pathlib).
    turn = oneshot(str(prompt), payload, model)
    try:
        for ev in turn:
            if ev.kind == "done":
                out.update(model=ev.meta.get("model") or model, cost_usd=ev.meta.get("cost_usd"))
                if ev.meta.get("error"):
                    raise RuntimeError(ev.text or "the turn failed")
                out["json"] = parsed(ev.text)
            elif ev.kind == "error":
                raise RuntimeError(ev.text or "the turn failed")
    except Exception as error:  # noqa: BLE001 — a failed turn settles nothing, and says why
        out.update(json={}, error=f"{type(error).__name__}: {error}"[:200])
    finally:
        turn.close()
    return out


def writer(model: str):
    """The error notebook's page writer (`notes.write_pages`) as host turns on `model`."""

    return lambda prompt, payload: host_json(prompt, payload, model)["json"]


def host_turn(model: str, state, questions: dict) -> dict:
    """One labeller turn: `{"answers", "model", "cost_usd"}`, `error` when it failed."""

    got = host_json(PROMPT, {"state": state, "questions": {n: shown(q) for n, q in questions.items()}}, model)
    said = got.pop("json").get("answers")
    return {**got, "answers": said if isinstance(said, dict) else {}}


def expected(question: dict, answer):
    """The answer as Laya's `expected`: a bool for a noul, an option name for a choice;
    `None` for an answer the question does not offer."""

    if not isinstance(answer, str):
        return None
    if question["type"] == "noul":
        return {"yes": True, "no": False}.get(answer.strip().lower())
    return answer if answer in (question.get("criteria") or {}) else None


def kept(answer) -> dict:
    """A labeller's answer as the notebook keeps it; a malformed one whole, under `raw`."""

    return answer if isinstance(answer, dict) else {"raw": answer}


def agreed(question: dict, a: dict | None, b: dict | None, intended=None) -> tuple[object, str | None]:
    """`(label, None)` when two labellers agree, else `(None, category)`: the error
    notebook's reason (`notes.CATEGORIES`) this question is dropped for. A generated
    state's `intended` answer must be the agreed one too."""

    def one(x):
        c = x.get("confidence") if isinstance(x, dict) else None
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:   # NaN fails the range
            return None
        return expected(question, x.get("answer"))

    va, vb = one(a), one(b)
    if va is None or vb is None:
        return None, "invalid"
    if va != vb:
        return None, "disagree"
    if min(a["confidence"], b["confidence"]) < AGREE:
        return None, "unsure"
    if intended is not None and va != intended:
        return None, "teacher_miss"
    return va, None


def label(kind: str, split: str, target: int, labellers: list[str], max_turns: int,
          turn=host_turn, log=print, round: int = 0, slice="base") -> dict:
    """Label `split` of `kind` up to `target` questions; returns what happened: this run's
    `turns` and cost, and the whole split's `agreed` (labelled questions), `dropped`,
    `by_category` and `states`, so a resumed split reports its true agreement. Every
    dropped question goes to the error notebook under `round` and `slice`, once.

    A state's verdict is one line of `<split>.judged.jsonl`, written first: its labelled row
    and its notes are written after it, and a rerun writes whichever of them an
    interruption left out. A judged state is never judged again; one interrupted before
    its verdict is judged again from the ledger's turns, without paying twice.
    `turn(model, state, questions)` is the labeller call (a stub in tests)."""

    if len(labellers) != 2 or labellers[0] == labellers[1]:
        raise ValueError("two different labellers")
    folder = DATA / kind
    ledger_path, out_path = folder / f"{split}.ledger.jsonl", folder / f"{split}.jsonl"
    judged_path = folder / f"{split}.judged.jsonl"
    ledger = {(r["state_hash"], r["labeller"]): r for r in read(ledger_path) if "error" not in r}
    written = {r["provenance"]["state_hash"] for r in read(out_path)}
    noted = {r["id"] for r in notes.ledger(kind)}

    def settle(verdict):
        """Write what `verdict` holds and an interruption may have left out."""
        if verdict["row"] and verdict["state_hash"] not in written:
            append(out_path, verdict["row"])
            written.add(verdict["state_hash"])
        for note in verdict["notes"]:
            if notes.note_id(kind, note["round"], note["category"], note["state_hash"], note["question"]) not in noted:
                noted.add(notes.record(kind, note.pop("category"), **note)["id"])

    judged = read(judged_path)
    for verdict in judged:
        settle(verdict)
    done = {r["state_hash"] for r in judged}
    have = sum(r["labelled"] for r in judged)
    stats = {"turns": 0, "cost_usd": 0.0, "cost_unknown": 0, "agreed": have,
             "dropped": sum(r["dropped"] for r in judged), "by_category": {}, "states": len(judged)}
    for r in judged:
        for why, n in r["by_category"].items():
            stats["by_category"][why] = stats["by_category"].get(why, 0) + n
    for row in read(folder / f"{split}.candidates.jsonl"):
        if have >= target:
            break
        if row["state_hash"] in done:
            continue
        questions = {n: q["question"] for n, q in row["questions"].items() if q["kind"] == kind}
        said = {}
        for model in labellers:
            got = ledger.get((row["state_hash"], model))
            if got is None:
                if stats["turns"] >= max_turns:
                    log(f"turn limit {max_turns} reached")
                    return {**stats, "stopped": "turn_limit"}
                got = {"state_hash": row["state_hash"], "labeller": model, "at": time.time(),
                       **turn(model, row["state"], questions)}
                append(ledger_path, got)
                ledger[(row["state_hash"], model)] = got
                stats["turns"] += 1
                if got.get("cost_usd") is None:
                    stats["cost_unknown"] += 1
                else:
                    stats["cost_usd"] += got["cost_usd"]
            said[model] = got
        if any("error" in s for s in said.values()):
            continue   # a failed turn is retried on a rerun, never read as disagreement
        a, b = (said[m]["answers"] for m in labellers)
        labels, drops, dropped = {}, {}, []
        for n in questions:
            value, why = agreed(questions[n], a.get(n), b.get(n), (row.get("intended") or {}).get(n))
            if why is None:
                labels[n] = value
                continue
            drops[why] = drops.get(why, 0) + 1
            dropped.append({"category": why, "round": round, "slice": slice if slice == "base" else list(slice),
                            "state_hash": row["state_hash"], "source": row["id"], "question": n,
                            "intended": (row.get("intended") or {}).get(n), "state": row["state"],
                            "answers": [{"labeller": said[m]["model"], **kept(said[m]["answers"].get(n))}
                                        for m in labellers]})
        verdict = {"state_hash": row["state_hash"], "labelled": len(labels), "dropped": sum(drops.values()),
                   "by_category": drops, "notes": dropped,
                   "row": labels and {"state": row["state"], "questions": {n: questions[n] for n in labels},
                                      "expected": labels,
                                      "provenance": {"state_hash": row["state_hash"], "source": row["id"],
                                                     "labellers": [said[m]["model"] for m in labellers]}}}
        append(judged_path, verdict)
        settle(verdict)
        have += len(labels)
        done.add(row["state_hash"])
        stats["agreed"], stats["dropped"], stats["states"] = have, stats["dropped"] + sum(drops.values()), \
            stats["states"] + 1
        for why, n in drops.items():
            stats["by_category"][why] = stats["by_category"].get(why, 0) + n
    return {**stats, "stopped": "target" if have >= target else "candidates_exhausted"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind")
    p.add_argument("split")
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--labellers", nargs=2, metavar="MODEL")
    p.add_argument("--max-turns", type=int, default=0)
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    rows = read(DATA / a.kind / f"{a.split}.candidates.jsonl")
    if a.dry or not a.labellers:
        first = rows[0] if rows else None
        print(json.dumps({"candidates": len(rows), "first_turn": first and {
            "state": first["state"],
            "questions": {n: shown(q["question"]) for n, q in first["questions"].items() if q["kind"] == a.kind}}},
            ensure_ascii=False, indent=1)[:4000])
        return
    print(json.dumps(label(a.kind, a.split, a.target, a.labellers, a.max_turns), indent=1))


if __name__ == "__main__":
    main()
