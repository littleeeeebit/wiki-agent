"""`python tool/eval/rollout.py status | canary <checkout>... | off | follow | rehearse [--out <file>]`

Stage 10's rollout: off -> shadow -> active, active first for named
checkouts only. Each command rewrites the app's saved settings
(`raw/jev/settings.json`, beside the hub's `.env`) and nothing else: no
document, index generation, trace or policy is touched, and a run already
going keeps the settings it started with.

    status     the saved settings and the mode this checkout and the hub get
    canary     active mode, limited to the named checkouts; every other shadow
    off        mode off: no Jev and no translator; baseline retrieval
    follow     no saved mode and no canary: the `.env`'s mode again

`rehearse` runs the outage and rollback rehearsal in a scratch hub, with
nothing sent anywhere: Jev's host is pointed at a closed local port. It
records a run and a pending approval, makes a worktree and a memory, starts
a run under the canary, fails over to baseline retrieval in the outage,
switches to off mid-run and back, and checks that the checkout, the
memory, the history, the pending approval and the worktree came through
unchanged, and that each run kept the settings it started with. Exit 1 when
any check failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("WIKI_SEARCH", "off")   # the rehearsal's corpus is indexed here
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from eval.baseline import RAW  # noqa: E402

RESULT = "jev-rollout-rehearsal/1"


def saved() -> dict:
    path = decision.settings_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def update(**change) -> decision.Config:
    """The saved settings with `change` applied; what was not named stays."""

    return decision.save({**{k: v for k, v in saved().items() if k in ("mode", "disabled_sources", "limits",
                                                                        "active_projects")}, **change})


def status(project: Path) -> dict:
    from search import HUB

    return {"settings_file": str(decision.settings_file()), "saved": saved(),
            "this_checkout": decision.config(project).status(), "hub": decision.config(HUB).status()}


# -- the rehearsal ----------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout.strip()


def state_of(repo: Path, worktree: Path, memory: Path, runs: Path, approvals: Path) -> dict:
    """Everything the rollback must leave as it was, hashed."""

    def digest(folder: Path) -> dict:
        return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(folder.rglob("*")) if p.is_file()} if folder.exists() else {}

    return {"head": git(repo, "rev-parse", "HEAD"), "status": git(repo, "status", "--porcelain"),
            "files": digest(repo / "docs"), "worktrees": git(repo, "worktree", "list", "--porcelain"),
            "worktree_head": git(worktree, "rev-parse", "HEAD"), "memory": digest(memory),
            "approvals": digest(approvals), "history": sorted(p.name for p in runs.rglob("*.json"))}


def rehearse() -> dict:
    from main import decisions, knowledge
    from search import sources

    import search

    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"check": name, "ok": bool(ok), **({"detail": detail} if detail is not None else {})})

    with tempfile.TemporaryDirectory(prefix="jev-rollout-") as scratch:
        root = Path(scratch)
        hub, repo, worktree = root / "hub", root / "repo", root / "repo-wt"
        (hub / "operator").mkdir(parents=True)
        (hub / "operator" / "review.md").write_text("# Review\n\nRule. Every change gets one review round.\n",
                                                    encoding="utf-8")
        (hub / ".env").write_text("TYPESAFE_API_KEY=rehearsal-key\nWIKI_JEV_MODE=shadow\n", encoding="utf-8")
        (repo / "docs").mkdir(parents=True)
        (repo / "docs" / "ports.md").write_text("# Ports\n\nThe API listens on port 7310.\n", encoding="utf-8")
        memory = repo / ".wiki" / "memory"
        memory.mkdir(parents=True)
        (memory / "2026-09-20-login.md").write_text("# Login\n\nKeep password login.\n", encoding="utf-8")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "e@e"], ["config", "user.name", "e"],
                     ["add", "docs"], ["commit", "-qm", "corpus"], ["worktree", "add", "-q", "-b", "work",
                                                                     str(worktree)]):
            git(repo, *args)
        env = {"JEV_ENV": str(hub / ".env")}
        approvals = root / "actions"
        with patch.dict(os.environ, env), patch.object(search, "HUB", hub), patch.object(knowledge, "HUB", hub), \
                patch.object(decisions, "LOGS", approvals), patch.object(decision, "HOST", "127.0.0.1"), \
                patch.dict(decision.RESOLVED, {"found": None, "at": 0.0}):
            runs = knowledge.runs_root()
            # A pending approval: a proposal recorded, never admitted.
            offered = decisions.fix_offer()
            owner = {"repo_id": "rehearsal", "worktree_id": str(worktree), "session_id": None, "spec_id": "s1",
                     "spec_revision": "1.0", "head_oid": git(worktree, "rev-parse", "HEAD")}
            pending = decisions.propose("loop.fix", offered, {"findings": ["[P1] docs/ports.md:3 - wrong port"]},
                                        owner, cfg=decision.Config("shadow", decision.MODEL, "file"),
                                        budget=Budget(seconds=1.0, calls=0, candidates=0), occasion="r1",
                                        baseline="fix", asked=False)
            decisions.keep(("rehearsal", "s1"), pending)
            # History: one baseline run before anything changes.
            before_run = knowledge.Run(repo, "rehearsal", "Which port does the API listen on?",
                                       decision.config(repo))
            knowledge.prepare(before_run.question, repo, cfg=before_run.cfg, run=before_run, cache=None)
            before_run.finish("answered", None, None, answered="")
            before = state_of(repo, worktree, memory, runs, approvals)
            check("starts in shadow from the .env", decision.config(repo).mode == "shadow")

            # Canary: active for this checkout only.
            update(mode="active", active_projects=[str(repo)])
            check("canary: this checkout is active", decision.config(repo).mode == "active")
            check("canary: another checkout stays shadow", decision.config(root / "elsewhere").mode == "shadow")
            check("canary: the hub alone stays shadow", decision.config(hub).mode == "shadow")

            # Synthetic outage under active mode: baseline retrieval, the reason said, nothing published as checked.
            live = knowledge.Run(repo, "rehearsal", "Which port does the API listen on?", decision.config(repo))
            out = knowledge.prepare(live.question, repo, cfg=live.cfg, run=live, cache=None)
            found = [e["locator"]["path"] for e in out["evidence"]]
            check("outage: status unavailable, not a negative judgment", out["status"] == "unavailable",
                  {"reason": out["reason"]})
            check("outage: baseline retrieval still found the page", "docs/ports.md" in found, found)
            check("outage: the failure is named", (out["reason"] or "") in ("network", "timeout", "unavailable"),
                  out["reason"])

            # Off mid-run: the run keeps its snapshot; new work is off.
            update(mode="off")
            check("off: a run in flight keeps the mode it started with", live.cfg.mode == "active")
            check("off: new work is off", decision.config(repo).mode == "off")
            off = knowledge.prepare(live.question, repo, cfg=decision.config(repo), cache=None)
            check("off: baseline retrieval, nothing asked", off["reason"] == "disabled" and not off["decisions"]
                  and "docs/ports.md" in [e["locator"]["path"] for e in off["evidence"]])
            live.finish("verification_unavailable", out["reason"], None, answered="")

            # Rollback to the .env's mode.
            update(mode=None, active_projects=[])
            check("follow: the .env's shadow again", decision.config(repo).mode == "shadow"
                  and decision.config(repo).mode_source == "file")
            after = state_of(repo, worktree, memory, runs, approvals)
            for part in ("head", "status", "files", "worktrees", "worktree_head", "memory", "approvals"):
                check(f"survived: {part}", before[part] == after[part])
            check("survived: history, and the outage's run added to it",
                  set(before["history"]) <= set(after["history"]) and len(after["history"]) == len(
                      before["history"]) + 1)
            summary, events = knowledge.stored(before_run.id, repo)
            check("history: the first run still reads", summary is not None and len(events) > 1)
            check("pending approval: still on record, never executed",
                  [r["id"] for r in decisions.history(("rehearsal", "s1"))] == [pending["id"]]
                  and not decisions.history(("rehearsal", "s1"))[0]["outcomes"])
        # The cold indexes this made under the user cache belong to a scratch path nothing will ask for again.
        for folder in (repo, hub):
            shutil.rmtree(sources.records_folder(folder), ignore_errors=True)
    return {"schema": RESULT, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "ok": all(c["ok"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/rollout.py", description="Jev rollout and rollback")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    canary = sub.add_parser("canary")
    canary.add_argument("checkouts", type=Path, nargs="+")
    sub.add_parser("off")
    sub.add_parser("follow")
    rehearsal = sub.add_parser("rehearse")
    rehearsal.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "rehearse":
        result = rehearse()
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = args.out or RAW / f"rollout-rehearsal-{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for c in result["checks"]:
            print(f"{'ok ' if c['ok'] else 'FAIL'} {c['check']}")
        print(out)
        return 0 if result["ok"] else 1
    if args.command == "canary":
        missing = [str(p) for p in args.checkouts if not (p / ".git").exists()]
        if missing:
            parser.error(f"not a checkout: {', '.join(missing)}")
        update(mode="active", active_projects=[str(p.resolve()) for p in args.checkouts])
    elif args.command == "off":
        update(mode="off")
    elif args.command == "follow":
        update(mode=None, active_projects=[])
    print(json.dumps(status(Path.cwd()), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
