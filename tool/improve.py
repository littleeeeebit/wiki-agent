"""Explicit, resumable self-improvement experiments; no automatic deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from improvement import Experiment, Refused, summary


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run separately owned harness improvement experiments")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--scope", choices=("hub", "project"), required=True)
    parser.add_argument("--name", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--config", type=Path, required=True)
    for name in ("round", "run", "status", "handoff"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        experiment = Experiment(args.repo, args.scope, args.name)
        if args.command == "init":
            result = summary(experiment.initialize(args.config.resolve()))
        elif args.command == "round":
            result = summary(experiment.round())
        elif args.command == "run":
            state = experiment.read()
            while len(state["rounds"]) < state["contract"]["rounds"]:
                state = experiment.round()
                print(json.dumps(summary(state), ensure_ascii=False, allow_nan=False), flush=True)
            result = summary(state)
        elif args.command == "handoff":
            result = experiment.handoff()
        else:
            result = summary(experiment.read())
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (Refused, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
