"""The spec-views main experiment's report and its three judgments: `report --config eval/spec-views/<main>.json`.

It reads the fixed-spec journal and the full-pipeline journal (`spec_views_pipeline`) apart, adds paired success
differences and cost-per-success ratios with task-resampled 95% intervals, the secondary measures the config
defines, and judges three things separately, each by rules fixed in the config before the run: implementation
pass, experiment validity, and adoption. A hand-written block between `NOTES` markers in the Markdown file is kept
across regenerations, so failure explanations read from the journal survive a rerun of the report.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from eval.report import boot
from eval.spec_views import (ROOT, SPECS, Refused, _charged, admitted, conditions, finals, git, journal, rows)
from eval.spec_views_pipeline import arm
from eval.spec_views_report import report as condition_report
from main import spec_views

NOTES = ("<!-- notes: kept across regenerations -->", "<!-- /notes -->")
NEED = {"A": ("generation",), "B": ("generation", "enrichment"), "C": ("generation", "enrichment"),
        "D": ("generation", "enrichment", "selection")}


# -- before the run ---------------------------------------------------------------------------------

def held_out(cfg: dict) -> None:
    """Refuse a main config whose tasks meet the pilot's or any task tuned on (`held_out.excluded`)."""
    banned = set(cfg["held_out"]["excluded"])
    for rel in cfg["held_out"].get("configs") or []:
        banned |= {t["id"] for t in json.loads((ROOT / rel).read_text(encoding="utf-8"))["tasks"]}
    met = sorted(banned & {t["id"] for t in cfg["tasks"]})
    if met:
        raise Refused(f"tasks used in development cannot be in the main experiment: {', '.join(met)}")


def implementation(cfg: dict) -> dict:
    """The first task's tests at the code the cells run on, journalled once per invocation."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", *cfg["implementation"]["tests"]]
    began = time.time()
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return journal(cfg, {"kind": "implementation", "code_commit": git(ROOT, "rev-parse", "HEAD").strip(),
                         "command": " ".join(cmd[1:]), "ok": done.returncode == 0, "code": done.returncode,
                         "tail": done.stdout[-600:], "seconds": round(time.time() - began, 1)})


# -- measures ---------------------------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def per_task(cfg: dict, past: list[dict]) -> dict:
    """`{task: {condition: {"succ": [...], "usd": cost incl. its spec calls, "n": cells}}}` from final rows;
    cost keeps every attempt and every spec call the condition needs (as if it ran alone)."""
    real = cfg["implementer"]["name"]
    steps: dict[tuple, float] = {}
    for r in past:
        if r.get("kind") in ("generation", "enrichment", "selection"):
            steps[(r["task"], r["kind"])] = steps.get((r["task"], r["kind"]), 0) + _charged(r)
    out = {t["id"]: {c: {"succ": [], "usd": sum(steps.get((t["id"], s), 0) for s in NEED[c])} for c in conditions(cfg)}
           for t in cfg["tasks"]}
    for (t, c, _), r in finals(past).items():
        if r.get("implementer") == real and t in out and c in out[t]:
            out[t][c]["succ"].append(float(bool(r.get("success"))))
    for r in past:
        if r.get("kind") == "cell" and r.get("implementer") == real and r["task"] in out and r["condition"] in out[r["task"]]:
            out[r["task"]][r["condition"]]["usd"] += _charged(r)
    return out


def paired(pt: dict, x: str, y: str, cfg: dict) -> dict:
    """Success difference x − y and cost-per-success ratio x / y over tasks both ran, with 95% intervals."""
    both = [t for t in pt if pt[t][x]["succ"] and pt[t][y]["succ"]]
    rs, seed = cfg["report"]["resamples"], cfg["seed"]
    diff = boot(both, lambda g: _mean([_mean(pt[t][x]["succ"]) - _mean(pt[t][y]["succ"]) for t in g]), rs, seed, 0.95)

    def ratio(g):
        cps = []
        for c in (x, y):
            s = sum(sum(pt[t][c]["succ"]) for t in g)
            cps.append(sum(pt[t][c]["usd"] for t in g) / s if s else float("inf"))
        if cps[1] == float("inf"):
            return None if cps[0] == float("inf") else 0.0
        return cps[0] / cps[1]

    return {"success_difference": diff, "cost_ratio": boot(both, ratio, rs, seed, 0.95)}


def spec_measures(cfg: dict, specs_of: dict[str, list[dict]]) -> dict:
    """Per condition with requirements: unsupported policy additions and unnecessary holds, over the specs it ran."""
    out = {}
    for c, records in specs_of.items():
        reqs = [r.get("requirements") or [] for r in records]
        out[c] = {"specs": len(records),
                  "unsupported_policy_additions": sum(1 for rs in reqs for r in rs if r.get("source", {}).get("kind")
                                                      == "policy" and r.get("status") in ("assumed", "undecided")),
                  "unnecessary_holds": sum(1 for r in records if r.get("blocked"))}
    return out


def cell_measures(past: list[dict], cfg: dict) -> dict:
    """Per condition from the final rows: missing critical conditions (code changed, affected tests kept, a hidden
    test still failed) and up to two representative failures per cause, read from the journal."""
    real, out = cfg["implementer"]["name"], {}
    for c in conditions(cfg):
        runs = [r for (_, cc, _), r in sorted(finals(past).items()) if cc == c and r.get("implementer") == real]
        missing = [r for r in runs if not r.get("success") and r.get("failure") == "verification"
                   and any(not re.search(r"(^|/)test_[^/]*\.py$", p) for p in r.get("changed") or [])
                   and all(a["ok"] for a in (r.get("affected") or {}).values())
                   and any(v != "passed" for v in (r.get("hidden") or {}).values())]
        examples: dict[str, list] = {}
        for r in runs:
            if not r.get("success") and len(examples.setdefault(r.get("failure") or "unknown", [])) < 2:
                examples[r.get("failure") or "unknown"].append(_example(r))
        out[c] = {"missing_critical_conditions": len(missing), "examples": examples}
    return out


def criticals(past: list[dict], cfg: dict, cand: str, base: str = "A") -> tuple[dict, dict]:
    """Critical regressions of `cand` and `base` over the (task, repetition) pairs both have a final row for,
    and how many of the candidate's changed a forbidden path or deleted a tracked file."""
    real = cfg["implementer"]["name"]
    got = {k: r for k, r in finals(past).items() if r.get("implementer") == real}
    pairs = {(t, n) for (t, c, n) in got if c == cand} & {(t, n) for (t, c, n) in got if c == base}
    count = {c: sum(1 for t, n in pairs if got[(t, c, n)].get("critical_regression")) for c in (cand, base)}
    hard = sum(1 for t, n in pairs if got[(t, cand, n)].get("forbidden") or got[(t, cand, n)].get("deleted"))
    return {"critical": count[cand], "hard": hard, "pairs": len(pairs)}, {"critical": count[base]}


def _example(r: dict) -> dict:
    return {"cell": f"{r['task']}/{r['condition']}{r['repetition']}", "cause": r.get("cause"),
            "hidden": {k: v for k, v in (r.get("hidden") or {}).items() if v != "passed"},
            "affected_failed": {k: a.get("failed") for k, a in (r.get("affected") or {}).items() if not a["ok"]},
            "forbidden": r.get("forbidden") or [], "changed": r.get("changed_count"),
            "error": (r.get("error") or "")[:240], "claimed_done": r.get("done_report") is not None,
            "wall_seconds": r.get("wall_seconds"), "usd": r.get("usd")}


# -- judgments -------------------------------------------------------------------------------------

def blocking(cand: dict, base: dict) -> tuple[int, str]:
    """The config's critical-regression rule, relative to A: the candidate's forbidden changes and deleted tracked
    files block outright; its other critical regressions block only when it has more of them than A."""
    hard, extra = cand["hard"], cand["critical"] - base["critical"]
    if hard:
        return hard, f"{hard} candidate cells changed a forbidden path or deleted a tracked file"
    if extra > 0:
        return extra, f"the candidate has {cand['critical']} critical regressions against A's {base['critical']}"
    return 0, f"critical regressions {cand['critical']} against A's {base['critical']}, none forbidden or deleting"


def adoption(cmp: dict, critical: tuple[int, str], rule: dict) -> dict:
    """One comparison against the config's paths: `adopt`, `reject` when the intervals exclude both paths or a
    critical regression blocks it (`blocking`), else `undetermined` (further verification needed)."""
    d, r = cmp["success_difference"], cmp["cost_ratio"]
    critical, said = critical
    if critical:
        return {"verdict": "reject", "quality": False, "cost": False, "why": f"a critical regression blocks it: {said}"}
    if d.get("low") is None or r.get("low") is None:
        return {"verdict": "undetermined", "why": "no interval: fewer than two tasks ran both conditions, or none succeeded"}
    q, k = rule["quality"], rule["cost"]
    quality = d["value"] >= q["min_difference"] and d["low"] > q["min_low"] and r["high"] <= q["max_ratio_high"]
    cost = d["low"] >= k["min_low"] and r["value"] <= k["max_ratio"] and r["high"] < k["max_ratio_high"]
    out_q = d["high"] < q["min_difference"] or r["low"] > q["max_ratio_high"]
    out_k = d["high"] < k["min_low"] or r["low"] > k["max_ratio"]
    grounds = (f"difference {d['value']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}], ratio {r['value']:.3f} "
               f"[{r['low']:.3f}, {r['high']:.3f}]; {said}")
    if quality or cost:
        return {"verdict": "adopt", "quality": quality, "cost": cost, "why": grounds}
    if out_q and out_k:
        return {"verdict": "reject", "quality": False, "cost": False, "why": f"the intervals exclude both paths; {grounds}"}
    return {"verdict": "undetermined", "quality": False, "cost": False,
            "why": f"neither path passes and the intervals still allow one; {grounds}"}


def validity(cfg: dict, fixed: list[dict], piped: list[dict], left: list[str]) -> dict:
    """Fixed config, no leakage or contamination, every result and cost recorded."""
    checks = {}
    cells = [r for r in fixed + piped if r.get("kind") == "cell"]
    checks["config"] = all(r.get("config_digest") == cfg["config_digest"] for r in cells)
    commits = {r.get("code_commit") for r in cells if r.get("code_commit")}
    checks["one_code_commit"] = len(commits) <= 1
    try:
        held_out(cfg)
        checks["held_out"] = True
    except Refused:
        checks["held_out"] = False
    try:
        for t in cfg["tasks"]:
            admitted(cfg, t, Path(cfg.get("specs_dir") or SPECS))
        checks["frozen_inputs"] = True
    except (Refused, OSError, KeyError) as exc:
        checks["frozen_inputs"] = str(exc)[:200]
    checks["costs_recorded"] = all(r.get("usd") is not None or r.get("usd_bound") is not None for r in cells)
    checks["all_cells_ran"] = not left
    return {"ok": all(v is True for v in checks.values()), "checks": checks, "code_commits": sorted(commits)}


def report(cfg: dict) -> dict:
    pipe = arm(cfg)
    fixed_past, pipe_past = rows(cfg), rows(pipe)
    fixed, piped = condition_report(cfg), condition_report(pipe)
    pt_f, pt_p = per_task(cfg, fixed_past), per_task(pipe, pipe_past)
    cand = cfg["candidate"]["condition"]
    comparisons = {f"{x}-{y}": paired(pt_f, x, y, cfg) for x, y in cfg["pairs"]}
    comparisons["pipeline " + "-".join(cfg["pipeline"]["pairs"][0])] = paired(pt_p, *cfg["pipeline"]["pairs"][0], cfg)
    frozen = {c: [] for c in conditions(cfg) if c != "A"}
    for t in cfg["tasks"]:
        try:
            rec = json.loads((Path(cfg.get("specs_dir") or SPECS) / f"{t['id']}.json").read_text(encoding="utf-8"))
        except OSError:
            continue
        for c in frozen:
            frozen[c].append({"requirements": rec["enriched"].get("requirements"), "blocked": spec_views.blocked(rec["enriched"])})
    generated = [r for r in pipe_past if r.get("kind") == "pipeline_spec" and r.get("spec_digest")]
    pipe_specs = {cand: [{"requirements": json.loads(Path(r["file"]).read_text(encoding="utf-8"))["enriched"]
                          .get("requirements"), "blocked": r.get("blocked")} for r in generated if Path(r["file"]).exists()]}
    # Implementation pass: the first task's tests (its feature, the setting-off byte identity, the harness) passed
    # at every code commit a counted cell ran on.
    impl = [r for r in fixed_past + pipe_past if r.get("kind") == "implementation"]
    # A pipeline cell whose spec step failed never started a session and carries no commit.
    commits = {r["code_commit"] for r in fixed_past + pipe_past if r.get("kind") == "cell" and r.get("code_commit")}
    implementation_pass = {"ok": bool(impl) and all(r["ok"] for r in impl) and {r["code_commit"] for r in impl} >= commits,
                           "runs": [{k: r.get(k) for k in ("code_commit", "command", "ok", "seconds")} for r in impl]}
    valid = validity(cfg, fixed_past, pipe_past, fixed["cells_not_run"] + piped["cells_not_run"])
    rule = cfg["adoption"]
    judged = {"fixed": adoption(comparisons[f"{cand}-A"], blocking(*criticals(fixed_past, cfg, cand)), rule),
              "pipeline": adoption(comparisons["pipeline " + "-".join(cfg["pipeline"]["pairs"][0])],
                                   blocking(*criticals(pipe_past, pipe, cand)), rule)}
    verdicts = [judged[k]["verdict"] for k in rule["requires"]]
    if not fixed["cells_ran"] and not piped["cells_ran"]:
        overall = "not run"
    elif "reject" in verdicts:
        overall = "reject"
    elif valid["ok"] and implementation_pass["ok"] and all(v == "adopt" for v in verdicts):
        overall = "adopt"
    else:
        overall = "undetermined"
    return {"schema": "spec-views-main/1", "experiment": cfg["experiment"], "pipeline_experiment": pipe["experiment"],
            "config_digest": cfg["config_digest"], "candidate": cfg["candidate"], "fixed": fixed, "pipeline": piped,
            "comparisons": comparisons, "specs": {"fixed": spec_measures(cfg, frozen), "pipeline": spec_measures(pipe, pipe_specs)},
            "cells": {"fixed": cell_measures(fixed_past, cfg), "pipeline": cell_measures(pipe_past, pipe)},
            "implementation": implementation_pass, "validity": valid, "adoption": {**judged, "overall": overall},
            "resume": {"fixed": f"python tool/eval/spec_views.py run --config {cfg['path']}",
                       "pipeline": f"python tool/eval/spec_views.py pipeline --config {cfg['path']}"}}


# -- the Markdown report ---------------------------------------------------------------------------

def _iv(d: dict, signed: bool = True) -> str:
    if not d or d.get("value") is None:
        return "n/a"
    f = "{:+.3f}" if signed else "{:.3f}"
    if d.get("low") is None:
        return f.format(d["value"]) + f" (n={d['n']}, no interval)"
    return f"{f.format(d['value'])} [{f.format(d['low'])}, {f.format(d['high'])}] (n={d['n']})"


def _conditions_table(rep: dict, part_name: str) -> list[str]:
    part, specs, cells = rep[part_name], rep["specs"][part_name], rep["cells"][part_name]
    lines = ["| Condition | Cells | Successes | Success rate [95% CI] | Hidden tests passed | Regressions | Critical "
             "| Missing critical conditions | Unsupported policy additions | Unnecessary holds | Infra reruns | Tokens "
             "| Wall h | USD incl. spec calls | USD per success |",
             "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for c, p in part["per_condition"].items():
        s = specs.get(c) or {}
        hp = p["hidden_pass_fraction"]
        lines.append(
            f"| {c} | {p['cells_run']} | {p['successes']} | {_iv(p['success_rate'], False)} | "
            f"{'n/a' if hp is None else format(hp, '.3f')} | {p['regressions']} | {p['critical_regressions']} | "
            f"{cells[c]['missing_critical_conditions']} | {s.get('unsupported_policy_additions', 'n/a')} | "
            f"{s.get('unnecessary_holds', 'n/a')} | {p['infra_reruns']} | {p['tokens']} | "
            f"{p['wall_seconds'] / 3600:.2f} | {round(p['usd'] + p['spec_generation_first_run_usd'], 4)} | "
            f"{p['cost_per_verified_success'] or 'no success'} |")
    return lines


def markdown(rep: dict, cfg: dict, kept: str = "") -> str:
    cand, j = cfg["candidate"]["condition"], rep["adoption"]
    word = {"adopt": "adopt", "reject": "do not adopt", "undetermined": "further verification needed",
            "not run": "not run"}
    pipe_key = "pipeline " + "-".join(cfg["pipeline"]["pairs"][0])
    held = {"A-B": "the whole enrichment step, its model-call cost included in B",
            "B-C": "information equal (`specs.equivalent` held for every task); only the format differs",
            "C-D": "execution equal; D narrows the input to the selection",
            f"{cand}-A": "the final candidate against A, frozen specs",
            pipe_key: "the final candidate against A, each repetition generated afresh from the original text"}
    f, p = rep["fixed"], rep["pipeline"]
    lines = [f"# Spec views — {rep['experiment']}: the main experiment", "",
             f"Outcome: **{word[j['overall']]}**. Candidate: condition {cand} — {cfg['candidate']['why']}", "",
             f"Implementer `{cfg['implementer'].get('model')}` at effort `{cfg['implementer'].get('effort')}`, the pilot's "
             f"harness, tools, retries, limits and isolation; {len(cfg['tasks'])} held-out tasks, {cfg['repetitions']} "
             f"repetitions fixed-spec and {cfg['pipeline']['repetitions']} in the pipeline. Config digest "
             f"`{rep['config_digest'][:12]}`. Fixed-spec spent USD {f['spent_usd']} in {f['elapsed_hours']} h (ceiling "
             f"USD {cfg['ceilings']['usd']}, {cfg['ceilings']['hours']} h); pipeline (`{rep['pipeline_experiment']}`) "
             f"USD {p['spent_usd']} in {p['elapsed_hours']} h (ceiling USD {cfg['pipeline']['ceilings']['usd']}, "
             f"{cfg['pipeline']['ceilings']['hours']} h).", "", *([cfg["context"], ""] if cfg.get("context") else []),
             "## Three judgments", "", "| Stage | Result | Grounds |", "| --- | --- | --- |"]
    impl = rep["implementation"]
    lines.append(f"| Implementation pass | {'pass' if impl['ok'] else 'not shown'} | "
                 + ("; ".join(f"`{r['command']}` at `{(r['code_commit'] or '')[:12]}`: {'passed' if r['ok'] else 'failed'}"
                              for r in impl["runs"]) or "no implementation check journalled") + " |")
    v = rep["validity"]
    lines.append(f"| Experiment validity | {'valid' if v['ok'] else 'not established'} | "
                 + ", ".join(f"{k} {'yes' if ok is True else ok or 'no'}" for k, ok in v["checks"].items()) + " |")
    for k in cfg["adoption"]["requires"]:
        lines.append(f"| Adoption, {k} | {word[j[k]['verdict']]} | {j[k]['why']} |")
    lines += ["", f"Adoption needs {' and '.join(cfg['adoption']['requires'])} to pass. " + cfg["adoption"]["text"], "",
              "## Comparisons", "",
              "Per task, the mean success over repetitions of one condition minus the other; the cost ratio divides "
              "the first condition's cost per verified success (every attempt and every spec call it needs) by the "
              "second's. Intervals resample tasks with their repetitions together (95% percentile bootstrap); a "
              "resample without a success for the first condition counts as an infinite ratio.", "",
              "| Comparison | Held equal | Success difference [95% CI] | Cost-per-success ratio [95% CI] |",
              "| --- | --- | --- | --- |"]
    for k, c in rep["comparisons"].items():
        lines.append(f"| {k} | {held.get(k, '')} | {_iv(c['success_difference'])} | {_iv(c['cost_ratio'], False)} |")
    lines += ["", "No significant difference is not evidence of equivalence: an interval that spans zero says the "
              "sample cannot tell the conditions apart.", "", "## Fixed specs", ""] + _conditions_table(rep, "fixed")
    lines += ["", f"## Full pipeline (`{rep['pipeline_experiment']}`)", "", cfg["pipeline"]["text"], ""]
    lines += _conditions_table(rep, "pipeline")
    lines += ["", "Measures:", "", *(f"- {k}: {d}" for k, d in cfg["metrics"]["definitions"].items()), "",
              "## Representative failures", "", "From the journal, up to two final rows per failure cause.", ""]
    for part_name in ("fixed", "pipeline"):
        for m in rep["cells"][part_name].values():
            for cause, xs in m["examples"].items():
                for x in xs:
                    lines.append(f"- {part_name} {x['cell']} — {cause}: hidden {x['hidden'] or 'all passed'}; "
                                 f"affected failing {x['affected_failed'] or 'none'}; {x['changed']} paths changed; "
                                 f"{'claimed done' if x['claimed_done'] else 'no done-report'}; "
                                 f"{x['wall_seconds']} s, USD {x['usd']}" + (f"; `{x['error']}`" if x["error"] else ""))
    lines += ["", NOTES[0], kept.strip() or "No explanation written yet.", NOTES[1], "", "## Not run", ""]
    for part_name, part in (("fixed", f), ("pipeline", p)):
        left = part["cells_not_run"]
        shown = ", ".join(left[:40]) + (", …" if len(left) > 40 else "")
        lines.append(f"- {part_name}: " + (f"{len(left)} cells not run ({shown}). Resume with "
                                           f"`{rep['resume'][part_name]}`; it needs the committed config unchanged, "
                                           "the same code commit, and room under that arm's ceilings, else a new "
                                           "experiment id." if left else "every cell ran."))
    lines += [f"- {x}" for x in cfg.get("not_run") or []]   # parts of the design this experiment leaves out
    stops = f["ceiling_stops"] + p["ceiling_stops"]
    lines += ["- Ceiling stops: " + (", ".join(f"{s['ceiling']} at USD {s['spent']}" for s in stops) or "none") + ".",
              "", "## The setting and its rollback", "", cfg["setting"], ""]
    return "\n".join(lines) + "\n"


def write(cfg: dict) -> Path:
    """The JSON report under `raw/` and the Markdown report, keeping its notes block."""
    rep = report(cfg)
    out = ROOT / "raw" / "eval" / "spec-views" / f"{cfg['experiment']}.report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = ROOT / cfg["report"]["markdown"]
    old = md.read_text(encoding="utf-8") if md.exists() else ""
    kept = old.split(NOTES[0], 1)[1].split(NOTES[1], 1)[0] if NOTES[0] in old and NOTES[1] in old else ""
    md.write_text(markdown(rep, cfg, "" if kept.strip() == "No explanation written yet." else kept), encoding="utf-8")
    return out
