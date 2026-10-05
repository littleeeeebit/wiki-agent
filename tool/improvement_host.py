"""Native role hosts for improvement experiments.

Without a profile both roles are refused: a native ChatSession cannot enforce a
hard call/token ceiling. The `refactor` profile (`tool/refactor_profile.py`)
accepts that ceiling as soft — the runner records a crossing — and its critic is
deterministic, because frozen characterization tests judge every candidate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common.process import background_options  # noqa: E402

TIERS = {
    "L0": "Mechanical only: delete dead code, rename locals, tidy formatting. Move nothing between files.",
    "L1": "Inside the listed files only: extract functions, merge duplicated helpers. Public names and "
          "signatures stay exactly as they are.",
    "L2": "Code may move between modules and new files may be created under the listed directories. Every "
          "public import path keeps working, re-exporting where needed.",
    "L3": "Contracts may change only as the step's goal and migration describe.",
}
SYSTEM = ("You propose one refactoring candidate in a throwaway checkout. Change structure only: frozen "
          "characterization tests you cannot edit judge behaviour, and debt metrics judge the improvement. "
          "Do not commit, push, open pull requests, delegate or ask questions. End with exactly one line: "
          "`Hypothesis: <what you changed and why it lowers debt>`.")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **background_options())
    if done.returncode:
        raise ValueError(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout


def captured(root: Path, start: str) -> tuple[str, list[str]]:
    """Everything the session changed since `start`, committed or not, as one
    binary patch and its paths; the checkout is reset to `start` for the
    runner, which applies the patch itself."""

    git(root, "add", "-A")
    patch = git(root, "diff", "--cached", "--binary", start)
    paths = [p for p in git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", start).split("\0") if p]
    git(root, "reset", "-q", "--hard", start)
    git(root, "clean", "-fdq")
    return patch, paths


def prompt(request: dict, spec: dict) -> str:
    failures = []
    for item in request.get("history", [])[-6:]:
        if item.get("verdict") in ("accepted", "lost"):
            continue
        shown = [r.get("diagnostic", "")[-1500:] for r in (item.get("evaluation") or {}).get("trials", [])
                 if r.get("reward", 1) < 1 and r.get("diagnostic")]
        failures.append(f"- round {item['round']} candidate {item['candidate']}: {item.get('reason', '')}\n"
                        + "\n".join(shown))
    return "\n\n".join(filter(None, [
        f"Goal: {spec['goal']}",
        f"Tier {spec['tier']}: {TIERS[spec['tier']]}",
        "Files in scope:\n" + "\n".join(f"- {f}" for f in spec["files"]),
        "Frozen tests (never edit):\n" + "\n".join(f"- {t}" for t in spec["tests"]),
        "Earlier candidates that failed — avoid what broke them:\n" + "\n".join(failures) if failures else "",
    ]))


def propose(request: dict, spec: dict, model: str, effort: str) -> dict:
    from agent import ChatSession

    root = Path(request["root"])
    start = git(root, "rev-parse", "HEAD").strip()
    chat = ChatSession(root, write=True, bypass=True, isolated=True, system=SYSTEM, model=model or None,
                       effort=effort or None)
    halt = threading.Event()

    def expire() -> None:
        halt.set()
        chat.stop(halt)

    timer = threading.Timer(request["limits"]["seconds"], expire)
    timer.daemon = True
    timer.start()
    final, tokens, failed = "", {}, ""
    try:
        for ev in chat.say(prompt(request, spec), halt):
            if ev.kind == "done":
                final, tokens = ev.text, ev.meta.get("tokens") or {}
            if ev.kind == "error" or ev.meta.get("error"):
                failed = ev.text or "host error"
    finally:
        timer.cancel()
        chat.close()
    patch, paths = captured(root, start)
    if type(tokens.get("in")) is not int or type(tokens.get("out")) is not int:
        # Never charged as zero: the runner stops on unknown usage.
        return {"error": failed or "the host reported no usage", "usage": {}}
    lines = [line for line in final.splitlines() if line.startswith("Hypothesis:")]
    hypothesis = lines[-1].removeprefix("Hypothesis:").strip() if lines else (failed or final.strip()[:200])
    # No paths makes the runner reject this candidate rather than stop the experiment.
    return {"edits": [{"component": "step", "hypothesis": hypothesis or "no change", "paths": paths}],
            "patch": patch, "usage": {"calls": 1, "tokens": tokens["in"] + tokens["out"]}}


def run(request: dict, model: str, effort: str, profile: str = "", spec: dict | None = None) -> dict:
    if request["stage"] not in ("propose", "critic"):
        raise ValueError("Native host adapter accepts only proposal or critic requests")
    if profile != "refactor":
        # Do not import/open ChatSession: ambient hooks and persistent state must
        # never be touched when the host cannot bound total input/output tokens.
        return {"error": "Native ChatSession hosts cannot enforce hard call/token ceilings; "
                         "configure an isolated, cap-enforcing domain adapter instead",
                "usage": {"calls": 0, "tokens": 0}, "role_model": model}
    if request["stage"] == "critic":
        return {"verdict": "accept", "reasons": ["frozen characterization tests judge refactor candidates"],
                "usage": {"calls": 0, "tokens": 0}}
    return propose(request, spec, model, effort)


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Native improvement roles; only the refactor profile runs")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else None
        result = run(json.load(sys.stdin), args.model, args.effort, args.profile, spec)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = {"error": str(exc), "usage": {"calls": 0, "tokens": 0}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
