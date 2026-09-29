"""`python tool/eval/actions.py [--out <file>]`

Stage 8's live gate: the three decision points this server owns, run
through their owner functions with real Jev in active mode, real English
normalization, and real retrieval over the smoke corpus (`eval/jev/smoke.json`)
when a choice gathers evidence.

    work.start    `[시작]` -> `work.run_turn`: send, gather evidence first, or ask
    specs.check   the done report -> `specs._check`: one registered check more, or none
    loop.fix      a refused round -> `loop.step`: send the findings, or gather context first

The host sessions, GitHub and the review cell are the stand-ins the test
suite uses (`test_specs.Worker`, `test_specs.Remote`, `test_loop.Hub`,
`test_loop.Reviewer`): what is measured is Jev's choice and what the owner
did with it, not a model's code. Each fixture is labelled with the
operation a careful operator would pick; the label is compared with the
choice, and the choice with the baseline — what the owner did before this
stage. Every state sent to Jev is checked for Korean prose on its way out.

Sends requests to TypeSafe: run it when a live check is wanted, not in the
test suite. Records under `raw/eval/jev/`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["WIKI_SEARCH"] = "off"   # the corpus is indexed here, never by the machine's daemon
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
import search  # noqa: E402
import translate  # noqa: E402
from eval.baseline import RAW, load, materialize, revision  # noqa: E402
from eval.decisions import ALONE, alone, watched  # noqa: E402
from main import app as main_app  # noqa: E402
from main import channels, decisions, knowledge, loop, specs, work  # noqa: E402
from main import query as chat  # noqa: E402
from test_loop import GATE, Hub, Reviewer, allow, deny, fixed, git, keep, pr_spec  # noqa: E402
from test_main import client  # noqa: E402
from test_specs import Remote, Worker, report  # noqa: E402

RESULT = "jev-actions-result/1"
MANIFEST = search.HUB / "eval" / "jev" / "smoke.json"
WAIT = 180.0
CHECKS = """
[checks]
docs-links = { cmd = "python checks/links.py", about = "checks that every Markdown link and heading under docs/ resolves" }
unit = { cmd = "python checks/unit.py", about = "runs the Python unit tests under src/" }
"""


def spec(slug: str, goal: str, done: list[str], files: list[str] = ()) -> dict:
    return {"slug": slug, "goal": goal, "out": [], "done": done,
            "grounds": {"pages": [], "files": list(files), "rules": []}, "decisions": []}


STARTS = [
    ("start-complete", "dispatch", spec("port-heading", "Rename the heading of docs/ports.md from 'Ports' to "
                                        "'Network ports'", ["docs/ports.md's first line reads '# Network ports'"],
                                        ["docs/ports.md:1"])),
    ("start-needs-facts", "evidence", spec("cache-home", "Move the translation cache to wherever the most recent "
                                           "recorded decision says it belongs",
                                           ["the cache path in the code matches the current decision"])),
    ("start-missing-acceptance", "clarify", spec("login-better", "Make login better", [])),
]
CHANGES = [
    ("check-docs", "check:docs-links", "docs/ports.md", "# Ports\n\nThe search daemon listens on port 8791. "
                                                         "See [owners](owners.md).\n"),
    ("check-code", "check:unit", "src/ports.py", "SEARCH_PORT = 8791\nCHAT_PORT = 8787\n"),
    ("check-neither", "none", "notes/todo.txt", "Ask the Atlas team about Tuesday.\n"),
]
FINDINGS = [
    ("fix-decision", "context", "[P1] src/cache.py:3 — the cache path is inside the checkout, which the recorded "
                                "translation cache decision no longer allows"),
    ("fix-local", "fix", "[P1] src/ports.py:2 — `range(n)` skips the last port; use `range(n + 1)`"),
]


def until(test, seconds: float = WAIT) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.1)
    raise TimeoutError("the owner did not finish in time")


def turn_done(path: str) -> bool:
    run = work._runs.get(path)
    return run is not None and run.done


def row(fixture: str, expected: str, record: dict | None, **extra) -> dict:
    if record is None:
        return {"fixture": fixture, "expected": expected, "asked": False, **extra}
    answer = record["jev"]["answer"] or {}
    return {"fixture": fixture, "point": record["point"], "expected": expected, "asked": True,
            "status": record["jev"]["status"], "basis": record["basis"], "choice": answer.get("choice"),
            "confidence": answer.get("confidence"), "probabilities": answer.get("probabilities"),
            "predicted": record["predicted"], "selected": record["selected"], "baseline": record["baseline"],
            "changed": record["selected"] != record["baseline"], "right": record["predicted"] == expected,
            "outcomes": record["outcomes"], "usage": record["jev"]["usage"],
            "replay": decisions.replay(record)["matches"], **extra}


def point(records: list[dict], name: str) -> dict | None:
    return next((r for r in records if r["point"] == name), None)


def setup(scratch: Path) -> SimpleNamespace:
    manifest = load(MANIFEST)
    hub, made = materialize(scratch, manifest["corpus"], None)
    repo = made.rename(scratch / "proj")
    search.HUB = knowledge.HUB = hub
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "e@e"], ["config", "user.name", "e"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "gate.py").write_text("import os, sys\nsys.exit(1 if os.path.exists('broken') else 0)\n",
                                  encoding="utf-8")
    (repo / "checks").mkdir()
    for name in ("links", "unit"):
        (repo / f"checks/{name}.py").write_text(f"print('{name} ok')\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/ports.py").write_text("SEARCH_PORT = 8791\n", encoding="utf-8")
    (repo / "src/cache.py").write_text("from pathlib import Path\n\nCACHE = Path('.cache/translate.sqlite3')\n",
                                       encoding="utf-8")
    (repo / ".wiki/adapter.toml").write_text(f'[slots]\ngate_cmd = "{GATE}"\n{CHECKS}', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "corpus")
    origin = scratch / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    return SimpleNamespace(repo=repo, origin=origin, tmp=scratch, hub=Hub(origin, scratch / "elsewhere"),
                           manifest=manifest)


def run() -> dict:
    cfg = decision.config()
    if cfg.mode == "off" or not cfg.key:
        raise SystemExit(f"Jev is not configured: {cfg.status()}")
    active = decision.Config("active", cfg.model, cfg.key_source, key=cfg.key)
    korean: list[str] = []
    real, decision.evaluate = watched(korean)
    kept, knowledge.falls_back = knowledge.falls_back, alone
    rows: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="jev-actions-") as scratch:
            w = setup(Path(scratch))
            web = client()
            with contextlib.ExitStack() as stack:
                for owner, name, value in (
                        (decision, "config", lambda: active), (channels, "LOCAL", {}), (chat, "LOGS", w.tmp / "chat"),
                        (chat, "_project", "proj"), (chat, "_config", {}), (chat, "_sessions", {}),
                        (chat, "_busy", {}), (main_app, "SWITCH", w.tmp / "main.json"),
                        (work, "LOGS", w.tmp / "work"), (work, "_sessions", {}), (work, "_busy", {}),
                        (work, "_runs", {}), (specs, "SPECS", w.tmp / "specs"), (decisions, "LOGS", w.tmp / "actions"),
                        (channels, "repo_for", lambda name: w.repo if name == "proj" else None),
                        (work, "ChatSession", Worker), (loop, "ChatSession", Reviewer),
                        (loop, "review_model", lambda: "codex:test"), (loop, "REVIEW", w.tmp / "review"),
                        (loop, "_cells", {}), (loop, "_loops", {}), (loop, "_seated", 0),
                        # The pull request's Korean: not what is measured, and not worth a request.
                        (translate, "translate", lambda texts, *a, **k: list(texts))):
                    stack.enter_context(patch.object(owner, name, value))
                source = {"focus": "next", "turn": 1.0, "session": "eval"}
                # work.start: the first turn of three specs.
                for fixture, expected, block in STARTS:
                    Worker.replies = ["Understood."]
                    sid = specs.answered(w.repo, [{"name": "spec", "value": block}], source)[0]["id"]
                    path = web.post(f"/api/specs/{sid}/start", json={}).json()["path"]
                    until(lambda: turn_done(path))
                    heard = [h for m in Worker.made if Path(m.path) == Path(path) for h in m.heard]
                    record = point(decisions.history(("proj", sid)), "work.start")
                    rows.append(row(fixture, expected, record, sent_turn=bool(heard),
                                    evidence_attached="Evidence the server retrieved" in (heard or [""])[0],
                                    fault=(specs.load("proj", sid) or {}).get("fault")))
                # specs.check: a done report after a change, with two registered checks.
                with patch.object(specs, "sh", Remote()), patch.object(loop, "kick", lambda *a: None):
                    for fixture, expected, rel, text in CHANGES:
                        def change(path, halt, rel=rel, text=text):
                            (path / rel).parent.mkdir(parents=True, exist_ok=True)
                            (path / rel).write_text(text, encoding="utf-8")
                            git(path, "add", "-A")
                            git(path, "commit", "-qm", f"change {rel}")
                            return report(True, True)

                        Worker.replies = [change]
                        block = spec(f"{fixture}-task", f"Update {rel}", [f"{rel} holds the new text"], [rel])
                        sid = specs.answered(w.repo, [{"name": "spec", "value": block}], source)[0]["id"]
                        path = web.post(f"/api/specs/{sid}/start", json={}).json()["path"]
                        until(lambda: turn_done(path))
                        record = point(decisions.history(("proj", sid)), "specs.check")
                        done = specs.load("proj", sid)
                        rows.append(row(fixture, expected, record, pr=(done.get("pr") or {}).get("number"),
                                        checks=done.get("checks"), fault=done.get("fault")))
                # loop.fix: a refused round, through the review loop.
                with patch.object(specs, "sh", w.hub):
                    for n, (fixture, expected, finding) in enumerate(FINDINGS, start=21):
                        Reviewer.replies = [deny(finding), allow, keep()]
                        Worker.replies = [fixed((finding, "fixed"))]
                        pr_spec(w, fixture, n)
                        loop.kick("proj", fixture)
                        until(lambda: ("proj", fixture) not in loop._loops)
                        heard = [h for m in Worker.made for h in m.heard if finding in h]
                        record = point(decisions.history(("proj", fixture)), "loop.fix")
                        rows.append(row(fixture, expected, record, state=specs.load("proj", fixture)["state"],
                                        evidence_attached=any("Evidence the server retrieved" in h for h in heard)))
                for running in list(loop._loops.values()):
                    running.stop()
                work.close_all()
    finally:
        decision.evaluate, knowledge.falls_back = real, kept
    asked = [r for r in rows if r["asked"]]
    usage = [r["usage"] or {} for r in asked]
    points = {}
    for r in asked:
        p = points.setdefault(r["point"], {"runs": 0, "changed": 0, "right": 0})
        p["runs"] += 1
        p["changed"] += r["changed"]
        p["right"] += r["right"]
    return {"schema": RESULT, "run": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                      **revision()},
            "jev": {"model": cfg.model, "prompt_version": decisions.VERSION,
                    "policy": decisions.policy(active).record()},
            "points": points, "korean_sent": korean, "fallback": ALONE,
            "usage": {"action_requests": len(usage), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                      "output_tokens": sum(u.get("output_tokens", 0) for u in usage)},
            "runs": rows}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/actions.py", description="Live agent decision gate")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    result = run()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RAW / f"actions-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "points": result["points"], "korean_sent": len(result["korean_sent"]),
                      "usage": result["usage"],
                      "runs": [{k: r.get(k) for k in ("fixture", "expected", "basis", "choice", "confidence",
                                                      "selected", "changed", "outcomes", "fault")}
                               for r in result["runs"]]}, ensure_ascii=False, indent=1))
    changed = [p for p, v in result["points"].items() if v["changed"]]
    return 1 if result["korean_sent"] or len(changed) < 3 else 0


if __name__ == "__main__":
    raise SystemExit(main())
