"""`python tool/ingest.py [--project <repo>] [--estimate] [--seconds 600]`

Index a repository's evidence — the hub's rules, its documents, decisions and
memories — into the evidence store, and normalize the Korean in it into
English ahead of the questions (`main.knowledge.ingest`). A turn then finds
its passages' English cached rather than translating them inside its budget.

`--estimate` prints how many texts and requests a run would take and sends
nothing. English text is never sent. Translation spends the Gemini key and
monthly limit of `translate`.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main.knowledge import ingest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/ingest.py", description="Index and normalize evidence")
    parser.add_argument("--project", default=None, help="the target repository; hub rules only without one")
    parser.add_argument("--estimate", action="store_true", help="count what would be sent; send nothing")
    parser.add_argument("--seconds", type=float, default=600.0, help="the whole run's translation time")
    args = parser.parse_args(argv)
    project = str(Path(args.project).expanduser().resolve()) if args.project else None
    print(json.dumps(ingest(project, args.seconds, args.estimate), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
