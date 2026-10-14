"""Training and scoring one kind with the `laya` package (plan stage 4).

`train` fine-tunes from a published checkpoint at its reviewed commit on the given splits'
labelled files, and fits temperatures on `calibration` when that split exists (never on
training items: `calib_frac=0`). `score` returns the validation questions a checkpoint gets
wrong and its answers; it scores the published checkpoints zero-shot too. Both are what
`loop.run` takes; the notebook calls them too. `laya` is imported only inside them, so
labelling runs without it.

No question is cut: one whose state and head exceed `MAX_LEN` is left out of training and
of scoring alike (stage 1's length rule).

Training runs on Colab (an L4, owner 2026-10-14). `pack` puts this folder's code and one
kind's labelled splits in a zip to upload there; `laya/notebooks/train.ipynb` takes it:

    python laya/train.py pack useful

This folder is not a package (no `__init__.py`): its modules import each other by bare
name, so `import laya` always finds the installed Laya package, never this folder.
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from label import DATA, read
from stage1 import CHECKPOINTS as PUBLISHED
from stage1 import REPO, WHOLE_HEAD, tokens

HERE = Path(__file__).resolve().parent
CHECKPOINTS = HERE / "checkpoints"
SPLITS = ("train", "validation", "calibration")
# The smallest of 1,024, 2,048 and 4,096 holding 95% of the kind's questions (plan stage 4):
# `useful` views are 1,396 tokens at p95, 0.3% over 2,048 (stage 1 lengths, 2026-10-13).
MAX_LEN = {"useful": 2048}


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


def fits(agent, row: dict, name: str, max_len: int) -> bool:
    return tokens(agent, row["state"], row["questions"][name]) <= max_len


def pack(kind: str) -> Path:
    """This folder's code and `kind`'s labelled splits, as `laya/...` in one zip, with
    `laya/packed.json` naming the commit and the splits' hashes the notebook's result
    carries back."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True).stdout.strip()

    out = DATA / kind / f"colab-{kind}.zip"
    packed = {"kind": kind, "commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--", ".")),
              "splits": {}}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(HERE.glob("*.py")):
            if not f.name.startswith("test_"):
                z.write(f, f"laya/{f.name}")
        for split in SPLITS:
            path = DATA / kind / f"{split}.jsonl"
            if path.exists():
                z.write(path, f"laya/data/{kind}/{split}.jsonl")
                packed["splits"][split] = hashlib.sha256(path.read_bytes()).hexdigest()
        z.writestr("laya/packed.json", json.dumps(packed, indent=1))
    return out


def merged(kind: str, splits: list[str], agent, max_len: int) -> tuple[Path, int]:
    """The splits' rows in Laya's format, each question that fits `max_len`; and how many
    questions were left out for length."""

    path, left = DATA / kind / f"merged-{'+'.join(splits)}.jsonl", 0
    with path.open("w", encoding="utf-8") as f:
        for split in splits:
            for row in read(DATA / kind / f"{split}.jsonl"):
                kept = [n for n in row["expected"] if fits(agent, row, n, max_len)]
                left += len(row["expected"]) - len(kept)
                if kept:
                    f.write(json.dumps({"state": row["state"], "questions": {n: row["questions"][n] for n in kept},
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
    config = {"option_layout": "parallel", **config}
    start = base_dir(base)
    reader = laya.load(start, device="cpu")   # its tokenizer only, to count lengths
    data, left = merged(kind, splits, reader, max_len)
    held = None   # validation, scored before and after by `finetune` (never trained on)
    if "validation" not in splits and (DATA / kind / "validation.jsonl").exists():
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


def score(kind: str, checkpoint, sure: dict | None = None) -> tuple[set[tuple[str, str]], dict]:
    """`(wrong, predicted)`: the validation `(state_hash, question)` the checkpoint gets
    wrong, and its answer to every validation question that fits `MAX_LEN`. `checkpoint`
    is a fine-tune's folder, or a published name (`base`, `typed`) for zero-shot. `sure`,
    when given, receives each answer's confidence (after the checkpoint's temperatures)."""

    import laya

    max_len = MAX_LEN[kind]
    agent = laya.load(base_dir(checkpoint) if checkpoint in PUBLISHED else str(checkpoint))
    calibration = Path(checkpoint) / "calibration.json"
    if checkpoint not in PUBLISHED and calibration.exists():
        agent.load_calibration(str(calibration))
    wrong, predicted = set(), {}
    for row in read(DATA / kind / "validation.jsonl"):
        asked = {n: row["questions"][n] for n in row["expected"] if fits(agent, row, n, max_len)}
        if not asked:
            continue
        got = agent.predict(row["state"], asked, max_len=max_len, head_max_len=WHOLE_HEAD)["answers"]
        for name in asked:
            key = (row["provenance"]["state_hash"], name)
            predicted[key] = answer(asked[name], got[name])
            if sure is not None:
                sure[key] = got[name]["confidence"]
            if predicted[key] != row["expected"][name]:
                wrong.add(key)
    return wrong, predicted


if __name__ == "__main__":
    if sys.argv[1:2] != ["pack"] or len(sys.argv) != 3:
        raise SystemExit("python laya/train.py pack <kind>")
    print(pack(sys.argv[2]))
