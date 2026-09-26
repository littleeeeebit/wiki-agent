"""`python tool/jev_search.py "<query>" --project <repo> [--k 8] [--state "<brief state>"]`

The search with the Jev retrieval controller: the same flow the app runs in
active mode (`main.knowledge.prepare`), on the same `.env` settings. Prints the
JSON dossier — evidence, routing, sufficiency, trace and the settings used.
Mode off, a missing key or any provider failure still returns baseline
retrieval, with the reason in the trace.

Moved out of `tool/search --jev`: the controller needs the `decision`
transport, and the `search` pipeline does not import another pipeline.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main.knowledge import prepare  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/jev_search.py", description="Jev-controlled search")
    parser.add_argument("query")
    parser.add_argument("--project", default=None, help="the target repository; hub rules only without one")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--state", default="", help="the current state Jev judges against")
    args = parser.parse_args(argv)
    if not 1 <= args.k <= 12:
        parser.error("--k must be between 1 and 12")
    project = str(Path(args.project).expanduser().resolve()) if args.project else None
    print(json.dumps(prepare(args.query, project, args.state, args.k), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
