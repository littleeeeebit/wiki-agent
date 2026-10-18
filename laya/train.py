"""Training and scoring one kind with the `laya` package (plan stage 4).

`train` fine-tunes from a published checkpoint at its reviewed commit on the given splits'
labelled files, and fits temperatures on `calibration` when that split exists (never on
training items: `calib_frac=0`). `score` returns the validation questions a checkpoint gets
wrong and its answers; it scores the published checkpoints zero-shot too. Both are what
`loop.run` takes; the notebook calls them too. `laya` is imported only inside them, so
labelling runs without it.

No question is cut: one whose state and head exceed `MAX_LEN` is left out of training and
of scoring alike (stage 1's length rule).

Training runs on Colab (an L4, owner 2026-10-14). `pack` puts this folder's code and the
given kinds' labelled splits in one zip to upload there; `laya/notebooks/train.ipynb` trains
each kind in it in turn:

    python laya/train.py pack analysis ask conflict progress redirect route source useful coverage

This folder is not a package (no `__init__.py`): its modules import each other by bare
name, so `import laya` always finds the installed Laya package, never this folder.
"""

import hashlib
import json
import random
import subprocess
import sys
import zipfile
from pathlib import Path

from kinds import LISTED, view
from label import DATA, read
from stage1 import CHECKPOINTS as PUBLISHED
from stage1 import REPO, WHOLE_HEAD, tokens

HERE = Path(__file__).resolve().parent
CHECKPOINTS = HERE / "checkpoints"
SPLITS = ("train", "validation", "calibration")
# The smallest of 1,024, 2,048 and 4,096 holding 95% of the kind's questions (plan stage 4):
# `useful` views are 1,396 tokens at p95, 0.3% over 2,048 (stage 1 lengths, 2026-10-13). On
# the labelled splits (2026-10-15) the other retrieval kinds are 277-684 at p95, at most 1,039.
# `coverage` reads every passage: 4,844 at p95, 40% over 4,096, so it takes ModernBERT's
# whole 8,192 (owner, 2026-10-15), one question a micro-batch to fit the L4.
MAX_LEN = {"useful": 2048, "coverage": 8192, "support": 1024,   # `support` views: 2,624 characters at most
           # `joint` views (2026-10-18): train 844 tokens median, 5.4% over 2,048, longest 3,439.
           "joint": 4096,
           **dict.fromkeys(("route", "source", "conflict", "redirect", "ask", "analysis", "progress"), 1024)}
MICRO_BATCH = {"coverage": 1, "joint": 2}   # others 4; accumulation keeps the effective batch at 64


def base_dir(name: str = "typed") -> str:
    """A published checkpoint's local folder at its reviewed commit (`stage1.load`'s pin);
    `laya.train.resolve_checkpoint_dir` would take the Hub's moving default."""

    from huggingface_hub import snapshot_download
    from laya.revisions import PINNED_REVISIONS

    sub = PUBLISHED[name]
    prefix = f"{sub}/" if sub else ""
    root = snapshot_download(REPO, revision=PINNED_REVISIONS[REPO], allow_patterns=[
        prefix + f for f in ("rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*")])
    return str(Path(root) / sub) if sub else root


def shown(kind: str, row: dict, name: str):
    """What the `kind` model is shown of `row` for question `name` (`kinds.view`). A passage
    kind's rows are stored with their view; a listed kind's view is taken here."""

    return view(name, kind, row["state"]) if kind in LISTED else row["state"]


def fits(agent, kind: str, row: dict, name: str, max_len: int) -> bool:
    return tokens(agent, shown(kind, row, name), row["questions"][name]) <= max_len


def pack(*kinds: str, picks: str | None = None) -> Path:
    """This folder's code and `kinds`' labelled splits, as `laya/...` in one zip, with
    `laya/packed.json` naming the kinds in training order, the commit and the splits'
    hashes the notebook's result carries back. `picks`, a support checkpoint's zip on
    Drive, has the notebook read coverage's train and calibration questions with it first
    (`support.evaluate`): the picks `joint` is asked over."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True).stdout.strip()

    out = DATA / "colab.zip"
    packed = {"kinds": list(kinds), "commit": git("rev-parse", "HEAD"),
              "dirty": bool(git("status", "--porcelain", "--", ".")), "splits": {}}
    if picks:
        packed["picks"] = {"checkpoint": picks, "splits": ["train", "calibration"]}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(HERE.glob("*.py")):
            if not f.name.startswith("test_"):
                z.write(f, f"laya/{f.name}")
        # `support` and `joint` are scored as the coverage answers they make (`support.evaluate`,
        # `joint.evaluate`); `joint` over a support run's picks.
        extra = [("coverage", "validation")] if {"support", "joint"} & set(kinds) and "coverage" not in kinds else []
        extra += [("joint", "picks-validation")] if "joint" in kinds else []
        extra += [("coverage", s) for s in packed.get("picks", {}).get("splits", [])]
        for kind, split in [(k, s) for k in kinds for s in SPLITS] + extra:
            path = DATA / kind / f"{split}.jsonl"
            if path.exists():
                z.write(path, f"laya/data/{kind}/{split}.jsonl")
                packed["splits"].setdefault(kind, {})[split] = hashlib.sha256(path.read_bytes()).hexdigest()
        z.writestr("laya/packed.json", json.dumps(packed, indent=1))
    return out


def subset(kind: str, n: int, seed: int = 0) -> tuple[str, int]:
    """A split of `n` train rows drawn with `seed`, for a learning curve: its name and how
    many rows it holds. Rows are read as records, so a torn tail or a blank line is not one."""

    rows = read(DATA / kind / "train.jsonl")
    random.Random(seed).shuffle(rows)
    name = f"train{n}"
    with (DATA / kind / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for row in rows[:n]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return name, len(rows[:n])


def merged(kind: str, splits: list[str], agent, max_len: int) -> tuple[Path, int]:
    """The splits' rows in Laya's format, each question that fits `max_len`; and how many
    questions were left out for length."""

    path, left = DATA / kind / f"merged-{'+'.join(splits)}.jsonl", 0
    with path.open("w", encoding="utf-8") as f:
        for split in splits:
            for row in read(DATA / kind / f"{split}.jsonl"):
                kept = [n for n in row["expected"] if fits(agent, kind, row, n, max_len)]
                left += len(row["expected"]) - len(kept)
                if kept:
                    # A listed kind's view is the same for each question of a row.
                    f.write(json.dumps({"state": shown(kind, row, kept[0]),
                                        "questions": {n: row["questions"][n] for n in kept},
                                        "expected": {n: row["expected"][n] for n in kept}}, ensure_ascii=False) + "\n")
    return path, left


def answer(question: dict, got: dict):
    """A prediction as `expected` reads: a bool for a noul, the option for a choice."""

    return got["noul"] >= 0.5 if question["type"] == "noul" else got["choice"]


def train(kind: str, splits: list[str], base: str = "typed", **config) -> Path:
    """A fine-tune of the published `base` (`stage1.CHECKPOINTS`) on `splits`, saved under
    `checkpoints/<kind>-<splits>`; temperatures fitted on `calibration` if it is labelled."""

    import laya
    from laya.train import TrainConfig, finetune

    max_len = MAX_LEN[kind]
    # Option order cannot matter (plan stage 4, L2); stage 1 found its requirements met.
    micro = MICRO_BATCH.get(kind, 4)
    config = {"option_layout": "parallel", "micro_batch": micro, "grad_accum": 64 // micro, **config}
    start = base_dir(base)
    reader = laya.load(start, device="cpu")   # its tokenizer only, to count lengths
    data, left = merged(kind, splits, reader, max_len)
    held = None   # validation, scored before and after by `finetune` (never trained on)
    # `finetune` scores it 16 questions a batch in FP32 whatever `micro_batch` is, which a
    # kind cut to one question a micro-batch cannot afford; `score` covers it there.
    if kind not in MICRO_BATCH and "validation" not in splits and (DATA / kind / "validation.jsonl").exists():
        held = str(merged(kind, ["validation"], reader, max_len)[0])
    out = CHECKPOINTS / f"{kind}-{'+'.join(splits)}"
    summary = finetune(str(data), start, str(out), TrainConfig(
        calib_frac=0.0, max_len=max_len, head_max_len=WHOLE_HEAD, eval_data=held, **config))
    (out / "laya-run.json").write_text(json.dumps({"kind": kind, "splits": splits, "base": base, "max_len": max_len,
                                                    "left_for_length": left, "config": config,
                                                    "summary": summary}, default=str, indent=1), encoding="utf-8")
    if not (DATA / kind / "calibration.jsonl").exists():
        return out
    agent = laya.load(str(out))
    pairs = [(r["state"], r["questions"],   # questions and targets: the same ones that fit
              {n: [0.0, 1.0] if v is True else [1.0, 0.0] if v is False
               else [1.0 if o == v else 0.0 for o in r["questions"][n]["criteria"]]
               for n, v in r["expected"].items()})
             for r in read(merged(kind, ["calibration"], agent, max_len)[0])]
    from laya.calibrate import records_from_labeled
    agent.fit_temperatures(records_from_labeled(agent, pairs))
    agent.save_calibration(str(out / "calibration.json"))
    return out


def loaded(checkpoint):
    """A Laya agent for a fine-tune's folder, with its temperatures, or for a published name."""

    import laya

    agent = laya.load(base_dir(checkpoint) if checkpoint in PUBLISHED else str(checkpoint))
    calibration = Path(checkpoint) / "calibration.json"
    if checkpoint not in PUBLISHED and calibration.exists():
        agent.load_calibration(str(calibration))
    return agent


def chance(got: dict, want) -> float:
    """The probability an answer gave the expected one: P(yes) or P(no) for a noul, the
    option's probability for a choice, where 1 - confidence is two other options' share."""

    return got["probabilities"].get(want, 0.0) if "probabilities" in got else got["noul"] if want else 1 - got["noul"]


def score(kind: str, checkpoint, sure: dict | None = None,
          truth: dict | None = None) -> tuple[set[tuple[str, str]], dict]:
    """`(wrong, predicted)`: the validation `(state_hash, question)` the checkpoint gets
    wrong, and its answer to every validation question that fits `MAX_LEN`. `checkpoint`
    is a fine-tune's folder, or a published name (`base`, `typed`) for zero-shot. `sure`,
    when given, receives each answer's probability (`answer_confidence`, max(p) after the
    checkpoint's temperatures, what ECE reads; a choice's `confidence` is entropy);
    `truth`, the probability it gave the expected answer (`chance`), what log loss reads."""

    max_len = MAX_LEN[kind]
    agent = loaded(checkpoint)
    wrong, predicted = set(), {}
    for row in read(DATA / kind / "validation.jsonl"):
        asked = {n: row["questions"][n] for n in row["expected"] if fits(agent, kind, row, n, max_len)}
        if not asked:
            continue
        got = agent.predict(shown(kind, row, next(iter(asked))), asked, max_len=max_len,
                            head_max_len=WHOLE_HEAD)["answers"]
        for name in asked:
            key = (row["provenance"]["state_hash"], name)
            predicted[key] = answer(asked[name], got[name])
            if sure is not None:
                sure[key] = got[name]["answer_confidence"]
            if truth is not None:
                truth[key] = chance(got[name], row["expected"][name])
            if predicted[key] != row["expected"][name]:
                wrong.add(key)
    return wrong, predicted


if __name__ == "__main__":
    args, picks = sys.argv[2:], None
    if args[:1] == ["--picks"]:
        picks, args = (args[1:2] or [None])[0], args[2:]
    if sys.argv[1:2] != ["pack"] or not (args or picks) or not set(args) <= set(MAX_LEN):
        raise SystemExit(f"python laya/train.py pack [--picks <support checkpoint zip>] <kind>... "
                         f"(kinds: {', '.join(MAX_LEN)})")
    print(pack(*args, picks=picks))
