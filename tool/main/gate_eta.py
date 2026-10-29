"""gate_eta — how long a running gate takes in all, from the same command's past runs and its own output.

Past run times are taken as log-normal, and a gate that has run `t` seconds already is still running: the
estimate is the median of the past runs longer than `t`. pytest's `[ 80%]` gives a second estimate `t / p`,
trusted more as `p` grows. The two are combined by precision in log space.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from statistics import NormalDist, fmean, stdev

PERCENT = re.compile(rb"\[\s*(\d{1,3})%\]")
# The variance of ln(t / p) at p = 1/2: tests do not take equal time, so the count is a biased clock.
# ponytail: a fixed guess; fit it from past runs' estimate errors once those are kept.
SPREAD = 0.25
FLOOR = 0.1   # past runs' smallest log spread: three equal runs do not make the next one certain
Z90 = NormalDist().inv_cdf(0.9)


def past(file: Path, repo: str, cmd: str, n: int = 10) -> list[float]:
    """The last `n` passing runs of `cmd` in `repo` that `keep` wrote to `file`, in seconds."""

    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue   # a line torn by a server that died while writing it
    return [r["seconds"] for r in rows if r.get("repo") == repo and r.get("cmd") == cmd][-n:]


def keep(file: Path, row: dict) -> None:
    with file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def progress(tail: bytes) -> float | None:
    """The last pytest percentage in an output tail, as a fraction."""

    found = PERCENT.findall(tail)
    return min(int(found[-1]), 100) / 100 if found else None


def estimate(past: list[float], t: float, p: float | None) -> dict | None:
    """The whole run in seconds as `{"total", "low", "high"}` (median and 80% range), for a gate that has
    run `t` seconds and printed progress `p`. `{"over": True}` when it outran its past runs and prints no
    progress; None with nothing to go on."""

    found = []   # (ln T, its variance)
    logs = [math.log(d) for d in past if d > 0]
    if len(logs) >= 3:
        mu, sigma = fmean(logs), max(stdev(logs), FLOOR)
        left = 1 - NormalDist(mu, sigma).cdf(math.log(max(t, 1e-6)))   # P(T > t)
        if left > 0.01:
            found.append((mu + sigma * NormalDist().inv_cdf(1 - left / 2), sigma ** 2))
        elif p is None:
            return {"over": True}
    if p is not None and p >= 0.02 and t >= 5:
        found.append((math.log(t / p), SPREAD * (1 - p) / p + 1e-4))
    if not found:
        return None
    weight = sum(1 / v for _, v in found)
    mid, sd = sum(m / v for m, v in found) / weight, math.sqrt(1 / weight)
    return {k: round(max(t, math.exp(mid + z * sd)), 1) for k, z in (("total", 0), ("low", -Z90), ("high", Z90))}
