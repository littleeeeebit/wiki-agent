"""Training and scoring one kind with the `laya` package (plan stage 4).

`train` fine-tunes from the base on the given splits' labelled files and fits temperatures
on `calibration` (never on training items: `calib_frac=0`). `score` returns the validation
questions the checkpoint gets wrong and its answers. Both are what `loop.run` takes; the notebook calls
them too. `laya` is imported only here, so labelling runs without it.

This folder is not a package (no `__init__.py`): its modules import each other by bare
name, so `import laya` always finds the installed Laya package, never this folder.
"""

import json
from pathlib import Path

from label import DATA, read

BASE = "convaiinnovations/laya"
CHECKPOINTS = Path(__file__).resolve().parent / "checkpoints"


def merged(kind: str, splits: list[str]) -> Path:
    path = DATA / kind / f"merged-{'+'.join(splits)}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for split in splits:
            for row in read(DATA / kind / f"{split}.jsonl"):
                f.write(json.dumps({k: row[k] for k in ("state", "questions", "expected")}, ensure_ascii=False) + "\n")
    return path


def answer(question: dict, got: dict):
    """A prediction as `expected` reads: a bool for a noul, the option for a choice."""

    return got["noul"] >= 0.5 if question["type"] == "noul" else got["choice"]


def train(kind: str, splits: list[str], base: str = BASE, max_len: int | None = None, **config) -> Path:
    import laya
    from laya.train import TrainConfig, finetune, resolve_checkpoint_dir

    out = CHECKPOINTS / f"{kind}-{'+'.join(splits)}"
    finetune(str(merged(kind, splits)), resolve_checkpoint_dir(base), str(out),
             TrainConfig(calib_frac=0.0, max_len=max_len, **config))
    agent = laya.load(str(out))
    pairs = [(r["state"], r["questions"],
              {n: [0.0, 1.0] if v is True else [1.0, 0.0] if v is False
               else [1.0 if o == v else 0.0 for o in r["questions"][n]["criteria"]]
               for n, v in r["expected"].items()})
             for r in read(DATA / kind / "calibration.jsonl")]
    from laya.calibrate import records_from_labeled
    agent.fit_temperatures(records_from_labeled(agent, pairs))
    agent.save_calibration(str(out / "calibration.json"))
    return out


def score(kind: str, checkpoint: Path) -> tuple[set[tuple[str, str]], dict]:
    """`(wrong, predicted)`: the validation `(state_hash, question)` the checkpoint gets
    wrong, and its answer to every validation question."""

    import laya

    agent = laya.load(str(checkpoint))
    calibration = Path(checkpoint) / "calibration.json"
    if calibration.exists():
        agent.load_calibration(str(calibration))
    wrong, predicted = set(), {}
    for row in read(DATA / kind / "validation.jsonl"):
        got = agent.predict(row["state"], row["questions"])["answers"]
        for name, want in row["expected"].items():
            key = (row["provenance"]["state_hash"], name)
            predicted[key] = answer(row["questions"][name], got[name])
            if predicted[key] != want:
                wrong.add(key)
    return wrong, predicted
