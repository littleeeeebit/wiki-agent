"""`python tool/eval/branches.py [--point P] [--limit N] [--out FILE]`

The form choices (`main.decisions.FORMS`) measured on their frozen fixtures,
`eval/jev/branches/<point>.json`. Each fixture is asked twice: the forms in
their registered order, then reversed, since Jev's Choice leans toward the
first option it reads. Per point: Jev's accuracy against the labels with a
95% Wilson interval, the share of fixtures whose two orders chose the same
form, and code's baseline accuracy beside them. A deferral or a failed ask
counts as wrong, and as a disagreement unless both orders failed alike.

The state is built by the owners' own functions (`pr_text.form_state`,
`refactor_survey.mode_state`), so what is measured is what production sends.
Sends two Jev requests per fixture and nothing else. Writes
`eval/jev/branches/results/<UTC stamp>.json`; `decisions.fitted` reads the
newest result naming a point and fits it only at `decisions.FIT`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import ACTION, Budget  # noqa: E402
from eval.report import wilson  # noqa: E402
from main import decisions, pr_text, refactor_survey  # noqa: E402
from main.query import ROOT  # noqa: E402

RESULT = "jev-branches/1"
WORKERS = 4   # `decision.MAX_IN_FLIGHT`


def case(point: str, fixture: dict) -> tuple[dict, str]:
    """`(the state Jev reads, code's baseline)` for one fixture."""

    if point == "pr.form":
        return pr_text.form_state(fixture["facts"]), pr_text.form_baseline(fixture["facts"])
    return refactor_survey.mode_state(fixture["spec"]), fixture["survey_mode"]


def asked(point: str, state: dict, order: list[str], cfg: decision.Config, ask=None) -> dict:
    """Jev's raw answer with the forms in `order`: its choice whatever the policy says, the
    probability it gave that choice, and whether the policy would accept it."""

    forms = decisions.FORMS[point]["forms"]
    try:
        jev = (ask or decisions.ask)(point, {f: forms[f] for f in order}, state, cfg, Budget(**ACTION),
                                     decisions.form_policy(cfg))
    except Exception as exc:  # noqa: BLE001 — a failed ask is a wrong answer, recorded
        return {"choice": None, "probability": None, "accepted": None, "status": f"error:{type(exc).__name__}"}
    answer = jev["answer"] or {}
    return {"choice": answer.get("choice"), "probability": (answer.get("probabilities") or {}).get(answer.get("choice")),
            "confidence": answer.get("confidence"), "accepted": jev["choice"], "status": jev["status"],
            "usage": jev.get("usage")}


def measure(point: str, items: list[dict], cfg: decision.Config, ask=None) -> dict:
    """One point's result over `items`, every row kept."""

    natural = list(decisions.FORMS[point]["forms"])

    def row(fixture: dict) -> dict:
        state, baseline = case(point, fixture)
        return {"id": fixture["id"], "sha256": fixture["sha256"], "label": fixture["label"], "baseline": baseline,
                "natural": asked(point, state, natural, cfg, ask),
                "reversed": asked(point, state, natural[::-1], cfg, ask)}

    with ThreadPoolExecutor(WORKERS) as pool:
        rows = list(pool.map(row, items))
    right = [(1.0 if r["natural"]["choice"] == r["label"] else 0.0, 1.0) for r in rows]
    same = [r["natural"]["choice"] == r["reversed"]["choice"] for r in rows]
    return {"n": len(rows), "accuracy": wilson(right, 0.95),
            "reversed_accuracy": wilson([(1.0 if r["reversed"]["choice"] == r["label"] else 0.0, 1.0) for r in rows],
                                        0.95),
            "order_agreement": round(sum(same) / len(rows), 4) if rows else None,
            "baseline_accuracy": round(sum(r["baseline"] == r["label"] for r in rows) / len(rows), 4) if rows else None,
            "rows": rows}


def evaluate(points: list[str], cfg: decision.Config, *, limit: int | None = None, root: Path = ROOT,
             ask=None) -> dict:
    """The result file's body. A run limited to fewer fixtures than the set records the set's
    digest it was drawn from but cannot fit a point: `fitted` compares the whole set's."""

    out = {"schema": RESULT, "ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "model": cfg.model,
           "form_version": decisions.FORM_VERSION, "fit": decisions.FIT, "points": {}}
    for point in points:
        items = decisions.fixtures(point, root)
        used = items[:limit] if limit else items
        got = measure(point, used, cfg, ask)
        got["fixtures_sha256"] = decisions.digest(used)
        got["fits"] = (got["accuracy"]["low"] or 0) >= decisions.FIT["low"] \
            and (got["order_agreement"] or 0) >= decisions.FIT["order_agreement"] and len(used) == len(items)
        out["points"][point] = got
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--point", choices=list(decisions.FORMS), action="append")
    parser.add_argument("--limit", type=int, help="the first N fixtures of each point only; fits nothing")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    cfg = decision.config()
    if not cfg.key:
        print("no Jev key: set TYPESAFE_API_KEY", file=sys.stderr)
        return 2
    result = evaluate(args.point or list(decisions.FORMS), cfg, limit=args.limit)
    out = args.out or ROOT / decisions.BRANCHES / "results" / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), **{p: {k: v for k, v in r.items() if k != "rows"}
                                          for p, r in result["points"].items()}}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
