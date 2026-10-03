"""Refuse native role hosts that cannot enforce the experiment's hard caps."""

from __future__ import annotations

import argparse
import json
import sys


def run(request: dict, model: str, effort: str) -> dict:
    if request["stage"] not in ("propose", "critic"):
        raise ValueError("Native host adapter accepts only proposal or critic requests")
    # Do not import/open ChatSession: ambient hooks and persistent state must
    # never be touched when the host cannot bound total input/output tokens.
    return {"error": "Native ChatSession hosts cannot enforce hard call/token ceilings; "
                     "configure an isolated, cap-enforcing domain adapter instead",
            "usage": {"calls": 0, "tokens": 0}, "role_model": model}


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Refuse an unsupported native improvement role")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    try:
        result = run(json.load(sys.stdin), args.model, args.effort)
    except (ValueError, KeyError, TypeError) as exc:
        result = {"error": str(exc), "usage": {"calls": 0, "tokens": 0}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
