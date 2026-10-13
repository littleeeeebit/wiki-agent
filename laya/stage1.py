"""Stage 1 measurements (`docs/plans/laya/1-serving.md`), with no paid call.

    python laya/stage1.py {lengths|latency|swap|zeroshot}

`lengths` counts, per kind, the tokens of every pooled question as Laya would encode it:
the whole state plus the question's head, nothing cut, and for a passage kind its view
too. It reads states and questions only, never an answer, so it may run before the gate
test is reserved. `latency` uses a filler state. `swap` and `zeroshot` read only the
development pool (`laya/gate.py`) and skip a question over `LIMIT`. Results go to
`raw/eval/laya/stage1-<command>-<timestamp>.json`.

Run with `laya/.venv` (torch and `laya` are installed there, not in the repository's Python).
"""

import json
import statistics
import sys
import time
from pathlib import Path

from kinds import PASSAGE, view
from store import read

ROOT = Path(__file__).resolve().parents[1]
POOL = Path(__file__).resolve().parent / "data" / "pool.jsonl"
OUT = ROOT / "raw" / "eval" / "laya"
# The bundle repository holds both; typed-decisions is its subfolder (`laya.router.DEFAULT_MODELS`).
REPO = "convaiinnovations/laya"
CHECKPOINTS = {"base": None, "typed": "typed-decisions"}
LIMITS = (512, 1024, 2048, 4096)
WHOLE_HEAD = 10**9   # a head budget no question reaches, so nothing of the question is cut


def load(name: str, device: str = "cpu"):
    """A published checkpoint at its reviewed commit, never the Hub's moving default."""

    import laya
    from laya.revisions import PINNED_REVISIONS

    return laya.load(REPO, subfolder=CHECKPOINTS[name], revision=PINNED_REVISIONS[REPO], device=device)


def tokens(agent, state, question: dict) -> int:
    """State plus head, as `build_sequence` lays them out before any clamp: the head is not
    capped by `head_max_len` and the state is not cut, so this is what the question needs."""

    from laya.agent import Agent
    from laya.common import build_head, encode_text, serialize_state

    head, _, _ = build_head(agent.tok, Agent._to_internal(question), head_max_len=WHOLE_HEAD)
    text = serialize_state(state).replace(agent.tok.mask_token, " ")
    return len(head) + len(encode_text(agent.tok, text, add_special_tokens=False)["input_ids"]) + 1


def summary(counts: list[int]) -> dict:
    counts = sorted(counts)
    return {"n": len(counts), "median": statistics.median(counts),
            "p95": counts[min(len(counts) - 1, round(0.95 * (len(counts) - 1)))], "max": counts[-1],
            **{f"over_{n}": round(sum(c > n for c in counts) / len(counts), 4) for n in LIMITS}}


def lengths() -> dict:
    rows = read(POOL)
    out = {}
    for name in CHECKPOINTS:
        agent = load(name)
        full: dict[str, list[int]] = {}
        seen: dict[str, list[int]] = {}
        for row in rows:
            for qname, q in row["questions"].items():
                full.setdefault(q["kind"], []).append(tokens(agent, row["state"], q["question"]))
                if q["kind"] in PASSAGE:
                    shown = view(qname, q["kind"], row["state"])
                    seen.setdefault(q["kind"], []).append(tokens(agent, shown, q["question"]))
        out[name] = {"max_len": agent.cfg.get("max_len"), "head_max_len": agent.cfg.get("head_max_len"),
                     "kinds": {k: summary(v) for k, v in sorted(full.items())},
                     "views": {k: summary(v) for k, v in sorted(seen.items())}}
    return {"pool_states": len(rows), "checkpoints": out}


def parallel() -> dict:
    """What `option_layout="parallel"` needs (`laya.common.uses_parallel_layout`, `Agent._infer`)
    against what is installed. No published checkpoint uses it; a fine-tune would opt in."""

    import transformers

    major = int(transformers.__version__.split(".")[0])
    return {"transformers": transformers.__version__, "transformers_ok": major >= 5,
            "backend": "eager only (no fast=, compile=, onnx)"}


FILLER = "The passage repeats a neutral sentence about configuration files and their owners. "
QUESTION = {"type": "noul", "instructions": "Does passage p0 help answer the question?"}


def latency(name: str = "typed", sizes=(512, 1024, 2304), questions=(1, 8), repeats=3) -> dict:
    """Seconds per request and peak GPU memory, warm, for a filler state of each token size
    (not a pooled state, so no gate state is read) with 1 and 8 questions: a `useful` request
    asks about 8 passages of one state. Laya retries a GPU out-of-memory on the CPU, so
    `cpu_fallbacks` says whether a number is a GPU one."""

    import torch

    started = time.perf_counter()
    agent = load(name, device="cuda")
    out = {"checkpoint": name, "load_seconds": round(time.perf_counter() - started, 2), "runs": []}
    for size in sizes:
        state = FILLER * (size // len(agent.tok(FILLER, add_special_tokens=False)["input_ids"]))
        for n in questions:
            asked = {f"useful_p{i}": QUESTION for i in range(n)}
            run = {"tokens": tokens(agent, state, QUESTION), "questions": n}
            try:
                agent.predict(state, asked, max_len=size + 64)   # warm-up
                torch.cuda.reset_peak_memory_stats()
                times = []
                for _ in range(repeats):
                    started = time.perf_counter()
                    agent.predict(state, asked, max_len=size + 64)
                    times.append(time.perf_counter() - started)
                run.update(seconds=round(statistics.median(times), 3),
                           peak_mib=torch.cuda.max_memory_allocated() // 2**20)
            except Exception as e:   # recorded, not raised: one size failing is a measurement
                run["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            run["cpu_fallbacks"] = agent.cpu_fallback_count
            out["runs"].append(run)
            print(run, flush=True)
            if "error" in run or agent.cpu_fallback_count:
                return out   # larger sizes only fail slower, on the CPU, filling RAM
    return out


LIMIT = 2304   # the longest passage view, 2,250 tokens, with room (`lengths`)


def development() -> list[dict]:
    """Pool states outside the gate test (`laya/gate.py`): the only ones read here."""

    import gate

    return gate.development(read(POOL), gate.reserved())


def asked(rows: list[dict]):
    """`(row, name, kind, question, state)` for every development question, `state` its view."""

    for row in rows:
        for name, q in row["questions"].items():
            yield row, name, q["kind"], q["question"], view(name, q["kind"], row["state"])


def reply(got: dict):
    return got["noul"] if "noul" in got else got["choice"]


def answers(agent, rows: list[dict], reverse: bool = False) -> tuple[dict, dict]:
    """Laya's answer to each development question that fits `LIMIT`, keyed `(state_hash,
    name)`, and per kind how many did not fit. `reverse` lists the options in reverse order
    (`option_order`); the answer is still reported by meaning, so it should not change."""

    got, over = {}, {}
    for row, name, kind, question, state in asked(rows):
        need = tokens(agent, state, question)
        if need > LIMIT:
            over[kind] = over.get(kind, 0) + 1
            continue
        if reverse:
            n = 2 if question["type"] == "noul" else len(question["criteria"])
            question = {**question, "option_order": list(range(n))[::-1]}
        # the question whole, as `tokens` counted it: the checkpoint's own `head_max_len`
        # (192 or 256) would cut a long claim question's instructions
        out = agent.predict(state, {name: question}, max_len=need, head_max_len=WHOLE_HEAD)
        if agent.cpu_fallback_count:
            raise RuntimeError(f"{name}: GPU out of memory at {need} tokens; Laya fell back to the CPU")
        got[(row["state_hash"], name)] = reply(out["answers"][name])
    return got, over


def same(a, b) -> bool:
    """One decision: a noul by the side of 0.5, a choice by its option."""

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (a >= 0.5) == (b >= 0.5)
    return a == b


def by_kind(pairs: dict, kinds_of: dict) -> dict:
    out: dict[str, list[bool]] = {}
    for key, agree in pairs.items():
        out.setdefault(kinds_of[key], []).append(agree)
    return {k: {"n": len(v), "share": round(sum(v) / len(v), 4)} for k, v in sorted(out.items())}


def swap() -> dict:
    """Measurement 3: the share of answers that change when only the option order changes
    (Laya issue #156), per checkpoint and kind."""

    rows = development()
    kinds_of = {(r["state_hash"], n): k for r, n, k, _, _ in asked(rows)}
    out = {"development_states": len(rows), "limit": LIMIT, "checkpoints": {}}
    for name in CHECKPOINTS:
        agent = load(name, device="cuda")
        straight, over = answers(agent, rows)
        reversed_, _ = answers(agent, rows, reverse=True)
        changed = {k: not same(straight[k], reversed_[k]) for k in straight}
        out["checkpoints"][name] = {"changed": by_kind(changed, kinds_of), "over_limit": over}
        del agent
    return out


def recorded(rows: list[dict]) -> dict:
    """Jev's answers as the local Langfuse recorded them for these states' observations,
    keyed `(state_hash, name)`: a noul's probability, a choice's option."""

    from pool import NAMES, observations

    ids = {r["id"]: r["state_hash"] for r in rows}
    out = {}
    for traced in NAMES:
        for o in observations(traced):
            if o["id"] not in ids:
                continue
            sent = o["output"] if isinstance(o["output"], dict) else json.loads(o["output"] or "{}")
            for name, value in sent.items():
                # a choice is recorded as {"choice", "confidence", "probabilities"}
                out[(ids[o["id"]], name)] = value["choice"] if isinstance(value, dict) else value
    return out


def zeroshot() -> dict:
    """Measurement 2: agreement of each published checkpoint, unchanged, with Jev's recorded
    answers on the development states. Agreement, not accuracy: neither side is a label."""

    rows = development()
    kinds_of = {(r["state_hash"], n): k for r, n, k, _, _ in asked(rows)}
    jev = recorded(rows)
    out = {"development_states": len(rows), "limit": LIMIT, "jev_answers": len(jev), "checkpoints": {}}
    for name in CHECKPOINTS:
        agent = load(name, device="cuda")
        got, over = answers(agent, rows)
        agree = {k: same(v, jev[k]) for k, v in got.items() if k in jev}
        out["checkpoints"][name] = {"agree": by_kind(agree, kinds_of), "over_limit": over}
        del agent
    return out


def write(command: str, result: dict) -> Path:
    import laya
    import torch

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"stage1-{command}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    meta = {"laya": laya.__version__, "torch": torch.__version__,
            "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    path.write_text(json.dumps({"command": command, **meta, **result}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


COMMANDS = {"lengths": lambda: {**lengths(), "parallel": parallel()}, "latency": lambda: latency(),
            "swap": swap, "zeroshot": zeroshot}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in COMMANDS:
        raise SystemExit(f"usage: python laya/stage1.py {{{'|'.join(COMMANDS)}}}")
    print(write(command, COMMANDS[command]()))
