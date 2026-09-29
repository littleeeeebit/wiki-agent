"""`python tool/relations.py [--project <repo>] check | extract | retire`

The knowledge graph of stage 4 of `docs/plans/jev/` (`main.knowledge`).

- `check`: index the repository — which builds the graph's structure — and
  check the graph: every edge valid, no dangling or out-of-scope endpoint,
  every span resolving to its original. Sends nothing.
- `extract [--limit 40] [--seconds 900] [--model M] [--estimate]`: a
  generative model proposes the entities and dependencies of passages not yet
  extracted, code keeps what it finds verbatim, and Jev judges support and
  bounded contradictions. `--estimate` counts and sends nothing. Proposals
  run the chat's CLI (`agent`); judgments spend the TypeSafe key.
- `retire`: stop using every extracted relationship; structure stays.
- `health`: the graph as the index holds it now, read-only — nothing is
  indexed or extracted (`GET /api/knowledge/graph/health` answers the same).

Exit 1 when the check finds a problem; `health` exits 1 unless the graph is `healthy`.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main.knowledge import FAILURES, check_graph, extract_graph, graph_health, retire_graph  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/relations.py", description="Build and check the knowledge graph")
    parser.add_argument("--project", default=None, help="the target repository; the hub without one")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    extract = commands.add_parser("extract")
    extract.add_argument("--limit", type=int, default=40, help="passages to extract in this run")
    extract.add_argument("--seconds", type=float, default=900.0, help="the whole run's time")
    extract.add_argument("--model", default="", help="the proposing model; the CLI's default without one")
    extract.add_argument("--estimate", action="store_true", help="count what would be sent; send nothing")
    commands.add_parser("retire")
    commands.add_parser("health")
    args = parser.parse_args(argv)
    project = str(Path(args.project).expanduser().resolve()) if args.project else None
    if args.command == "health":
        out = graph_health(project)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["status"] == "healthy" else 1
    if args.command == "extract":
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        out = extract_graph(project, args.limit, args.seconds, args.estimate, model=args.model)
    else:
        out = check_graph(project) if args.command == "check" else retire_graph(project)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    report = out.get("graph", out)
    return 1 if any(report.get(name) for name in FAILURES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
