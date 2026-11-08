"""The spec-views experiment's report: `python tool/eval/spec_views.py report --config <file>`.

Reads the journal `tool/eval/spec_views.py` keeps. Only rows from the configured implementer count as model
performance; a scripted stub's rows are counted apart and never reported as results.
"""

from __future__ import annotations

from eval.report import boot
from eval.spec_views import CONDITIONS, PAIRS, _charged, finals, pending, rows


def report(cfg: dict) -> dict:
    past = [r for r in rows(cfg) if r.get("config_digest") == cfg["config_digest"] or r.get("kind") != "cell"]
    real = cfg["implementer"]["name"]
    counted = {k: r for k, r in finals(past).items() if r.get("implementer") == real}
    mocked = sum(1 for r in finals(past).values() if r.get("implementer") != real)
    tasks = [t["id"] for t in cfg["tasks"]]
    rate = {(t, c): [float(bool(r.get("success"))) for (tt, cc, _), r in counted.items() if tt == t and cc == c]
            for t in tasks for c in CONDITIONS}
    resamples, seed = cfg["report"]["resamples"], cfg["seed"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    per_condition = {}
    for c in CONDITIONS:
        runs = [r for (_, cc, _), r in counted.items() if cc == c]
        # Outcomes come from the final row per cell; cost, tokens and time from every attempt, infra reruns too.
        attempts = [r for r in past if r.get("kind") == "cell" and r.get("implementer") == real and r["condition"] == c]
        per_condition[c] = {
            "cells_run": len(runs), "successes": sum(bool(r.get("success")) for r in runs),
            "success_rate": boot([t for t in tasks if rate[(t, c)]], lambda g, c=c: mean([mean(rate[(t, c)]) for t in g]),
                                 resamples, seed, 0.95),
            "attempts": len(attempts), "usd": round(sum(_charged(r) for r in attempts), 4),
            "tokens": sum(sum((r.get("tokens") or {}).values()) for r in attempts),
            "wall_seconds": round(sum(r.get("wall_seconds") or 0 for r in attempts), 1),
            "retries": sum(r.get("retries") or 0 for r in runs),
            "infra_reruns": sum(1 for r in past if r.get("kind") == "cell" and r.get("infra") and r["condition"] == c),
            "hidden_pass_fraction": mean([mean([v == "passed" for v in (r.get("hidden") or {}).values()] or [0])
                                          for r in runs]),
            "regressions": sum(1 for r in runs if any(not a["ok"] for a in (r.get("affected") or {}).values())),
            "forbidden": sum(1 for r in runs if r.get("forbidden")),
            "critical_regressions": sum(1 for r in runs if r.get("critical_regression")),
            "failures": _count(r.get("failure") for r in runs if not r.get("success")),
        }
    generation = _generation(past, tasks)
    for c in CONDITIONS:
        first_run = sum(generation["as_if_alone"][c].values())
        s = per_condition[c]["successes"]
        total = per_condition[c]["usd"] + first_run
        per_condition[c]["spec_generation_first_run_usd"] = round(first_run, 4)
        per_condition[c]["cost_per_verified_success"] = round(total / s, 4) if s else None
    diffs = {}
    for x, y in PAIRS:
        both = [t for t in tasks if rate[(t, x)] and rate[(t, y)]]
        diffs[f"{x}-{y}"] = boot(both, lambda g, x=x, y=y: mean([mean(rate[(t, x)]) - mean(rate[(t, y)]) for t in g]),
                                 resamples, seed, 0.95)
    ran = sorted(f"{t}/{c}{r}" for (t, c, r) in counted)
    left = sorted(f"{t}/{c}{r}" for (t, c, r) in pending(cfg, past))
    baselines = {k[0]: r for k, r in finals(past, "production_baseline").items()}
    stops = [r for r in past if r.get("kind") == "ceiling_stop"]
    return {"schema": "spec-views-report/1", "experiment": cfg["experiment"], "kind": cfg["kind"],
            "config_digest": cfg["config_digest"], "implementer": cfg["implementer"], "per_condition": per_condition,
            "paired_differences": diffs, "spec_generation": generation, "cells_ran": ran, "cells_not_run": left,
            "mock_rows_excluded": mocked, "production_baseline": {
                t: ({"success": r.get("success"), "gate": r.get("production_gate"), "usd": r.get("usd"),
                     "failure": r.get("failure")} if (r := baselines.get(t)) else {"not_run": _why_not(stops)})
                for t in tasks},
            "equivalence": {r["task"]: r["ok"] for r in past if r.get("kind") == "equivalence"},
            "ceiling_stops": stops, "spent_usd": round(sum(_charged(r) for r in past), 4),
            "elapsed_hours": round(sum(r.get("elapsed", 0) for r in past
                                       if r.get("kind") in ("invocation", "preparation")) / 3600, 3),
            "resume": f"python tool/eval/spec_views.py run --config {cfg['path']}",
            "adjustments": (cfg.get("adjustments") or []) + [r for r in past if r.get("kind") == "adjustment"],
            "code_commits": _count(r.get("code_commit") for r in counted.values()),
            "charged_at_cap": {"cells": sorted(f"{t}/{c}{n}" for (t, c, n), r in counted.items() if r.get("usd") is None),
                               "usd": round(sum(_charged(r) for r in counted.values() if r.get("usd") is None), 4)},
            "runner_difference": cfg["runner_difference"],
            "per_task": {t: {c: rate[(t, c)] for c in CONDITIONS} for t in tasks}}


def _why_not(stops: list[dict]) -> str:
    return f"the {stops[-1]['ceiling']} ceiling stopped the run first" if stops else "not reached yet"


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v or "unknown"] = out.get(v or "unknown", 0) + 1
    return out


def _generation(past: list[dict], tasks: list[str]) -> dict:
    """Spec-generation cost per task and step, and per condition as if that condition ran alone (first run);
    a repetition reuses the frozen spec at no further cost (cached)."""

    step: dict[tuple, float] = {}   # every attempt at a step, failed ones too
    for r in past:
        if r.get("kind") in ("generation", "enrichment", "selection"):
            step[(r["task"], r["kind"])] = step.get((r["task"], r["kind"]), 0) + _charged(r)
    need = {"A": ("generation",), "B": ("generation", "enrichment"), "C": ("generation", "enrichment"),
            "D": ("generation", "enrichment", "selection")}
    alone = {c: {t: round(sum(step.get((t, s), 0) for s in need[c]), 4) for t in tasks} for c in CONDITIONS}
    return {"per_task": {t: {s: step.get((t, s)) for s in ("generation", "enrichment", "selection")} for t in tasks},
            "as_if_alone": alone, "cached_repetition_usd": 0.0,
            "actually_spent_usd": round(sum(step.values()), 4)}


def markdown(rep: dict, cfg: dict) -> str:
    def iv(d):
        if not d or d.get("value") is None:
            return "n/a"
        return f"{d['value']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}] (n={d['n']})" if d.get("low") is not None \
            else f"{d['value']:+.3f} (n={d['n']}, no interval)"

    lines = [f"# Spec views — {rep['experiment']}: pilot, not the main experiment", "",
             "This is a pilot. It checks the harness, the B–C equivalence, the journal and the cost accounting. "
             "Its sample is far too small to support any adoption claim, and nothing here judges adoption.", "",
             f"Implementer: `{cfg['implementer'].get('model')}` at effort `{cfg['implementer'].get('effort')}`, "
             f"{cfg['repetitions']} repetitions, concurrency {cfg['concurrency']}, ceilings USD "
             f"{cfg['ceilings']['usd']} and {cfg['ceilings']['hours']} h. Config digest `{rep['config_digest'][:12]}`. "
             f"Spent USD {rep['spent_usd']} and {rep['elapsed_hours']} h, preparation included.", "",
             "## Verified task success per condition", "",
             "| Condition | Cells run | Successes | Success rate (95% CI over tasks) | Cell USD | First-run spec USD "
             "| USD per verified success | Regressions | Critical | Forbidden | Failures |",
             "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for c, p in rep["per_condition"].items():
        sr = p["success_rate"]
        shown = "n/a" if sr["value"] is None else (f"{sr['value']:.3f} [{sr['low']:.3f}, {sr['high']:.3f}]"
                                                   if sr["low"] is not None else f"{sr['value']:.3f}")
        cps = "not computable (no success)" if p["cost_per_verified_success"] is None else p["cost_per_verified_success"]
        lines.append(f"| {c} | {p['cells_run']} | {p['successes']} | {shown} | {p['usd']} | "
                     f"{p['spec_generation_first_run_usd']} | {cps} | {p['regressions']} | "
                     f"{p['critical_regressions']} | {p['forbidden']} | "
                     f"{', '.join(f'{k} {v}' for k, v in p['failures'].items()) or '–'} |")
    lines += ["", "Cost per verified success divides every run's cost, failed runs included, plus the condition's "
              "first-run spec-generation cost, by its verified successes.", "",
              "## Paired differences", "",
              "Per task, the mean success over repetitions of one condition minus the other; the interval resamples "
              "tasks with their repetitions together (95% percentile bootstrap).", "",
              "| Difference | Value [95% CI] |", "| --- | --- |"]
    lines += [f"| {k} | {iv(v)} |" for k, v in rep["paired_differences"].items()]
    g = rep["spec_generation"]
    lines += ["", "## Spec-generation cost", "",
              "Each condition as if it ran alone: A needs the generator's spec, B and C its enrichment too, D also "
              "its selection. Repetitions reuse the frozen spec (cached, USD "
              f"{g['cached_repetition_usd']}). Actually spent once for all: USD {g['actually_spent_usd']}.", "",
              "| Condition | First-run USD over all tasks |", "| --- | ---: |"]
    lines += [f"| {c} | {round(sum(v.values()), 4)} |" for c, v in g["as_if_alone"].items()]
    lines += ["", "## Production-runner baseline", "", rep["runner_difference"], "",
              "| Task | Result |", "| --- | --- |"]
    for t, b in rep["production_baseline"].items():
        lines.append(f"| {t} | " + (f"not run — {b['not_run']}" if "not_run" in b else
                                    f"success {b['success']}, gate {'passed' if (b.get('gate') or {}).get('ok') else 'failed'}"
                                    f", USD {b.get('usd')}") + " |")
    lines += ["", "## Cells", "", f"Ran ({len(rep['cells_ran'])}): " + (", ".join(rep["cells_ran"]) or "none") + ".", "",
              f"Not run ({len(rep['cells_not_run'])}): " + (", ".join(rep["cells_not_run"]) or "none") + ".", ""]
    if rep["ceiling_stops"]:
        s = rep["ceiling_stops"][-1]
        lines += [f"The {s['ceiling']} ceiling stopped the run at USD {s['spent']} and {s['hours']} h.", ""]
    cap = rep["charged_at_cap"]
    if cap["cells"]:
        lines += [f"{len(cap['cells'])} cells ended without a reported cost (a timeout or a crash cut the session before "
                  f"its result) and were charged at their cap, USD {cap['usd']} in total: {', '.join(cap['cells'])}. "
                  "The ceiling counts these bounds, so the spend actually billed is lower than the spend shown.", ""]
    lines += [f"Resume: `{rep['resume']}` runs the cells not yet run while the ceilings have room; beyond them a new "
              "experiment id with its own committed config is required.", "",
              f"Rows from a scripted stub implementer are never counted here ({rep['mock_rows_excluded']} excluded).", "",
              "Code commits the counted cells ran on: "
              + (", ".join(f"`{k[:12]}` ({v})" for k, v in rep["code_commits"].items()) or "none") + ".", "",
              "## Equivalence", "", "| Task | B–C equivalent |", "| --- | --- |"]
    lines += [f"| {t} | {ok} |" for t, ok in rep["equivalence"].items()]
    lines += ["", "## Adjustments during the pilot", ""]
    lines += [f"- {a['what']} — " + (f"tuned on {', '.join(a['tasks'])}; the main experiment must exclude these tasks."
                                     if a.get("tasks") else "not tuned on any task.")
              for a in rep["adjustments"]] or ["- None."]
    return "\n".join(lines) + "\n"
