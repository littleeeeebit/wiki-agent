"""Explicit CLI-backed architecture analysis, independent of frontend builds."""

import sys
import argparse
from pathlib import Path

from main.architecture import scan


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    result = scan(args.repo, model=args.model)
    print(f"OMM: {len(result['nodes'])} elements analyzed and written through the CLI")
