"""`python tool/jev_search.py "<query>" --project <repo> [--k 8] [--state "<brief state>"] [--retrieval]`

The search with Jev's decision workflow: the same flow the app runs in
active mode (`main.knowledge.prepare`), on the same `.env` settings. Prints the
JSON dossier — status, evidence, requirements, transitions, decisions and the
settings used. Mode off, a missing key or any provider failure still returns
baseline retrieval, `unavailable` with the reason.

`--external` lets a repair round search arXiv, which sends the question
outside. `--record <file>` writes the run's tape — its English, answers,
rounds and clock, source text included — for `--replay <file>`, which runs
the same decisions again from it with no request and no search, and prints
whether every transition came out the same.

`--retrieval` prints stage 5's round instead (`main.knowledge.retrieve`):
the RetrievalRequest and its chunk-level RetrievalResult, graph lane and
paths included, unless `WIKI_GRAPH_RETRIEVAL=off`.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main.knowledge import MAX_K, prepare, replay, retrieve  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/jev_search.py", description="Jev-controlled search")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--project", default=None, help="the target repository; hub rules only without one")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--state", default="", help="the current state Jev judges against")
    parser.add_argument("--external", action="store_true", help="let a repair round search arXiv")
    parser.add_argument("--record", type=Path, help="write the run's tape to this file")
    parser.add_argument("--replay", type=Path, help="decide a recorded tape again; nothing is sent")
    parser.add_argument("--retrieval", action="store_true",
                        help="print one round of chunk-level retrieval with the graph lane instead of the dossier")
    args = parser.parse_args(argv)
    if args.replay:
        out = replay(json.loads(args.replay.read_text(encoding="utf-8")))
        print(json.dumps({k: out[k] for k in ("matches", "prompt_changed", "transitions", "recorded")},
                         ensure_ascii=False, indent=2))
        return 0 if out["matches"] else 1
    if not args.query:
        parser.error("a query is required")
    project = str(Path(args.project).expanduser().resolve()) if args.project else None
    if args.retrieval:
        if not 1 <= args.k <= 40:
            parser.error("--k must be between 1 and 40")
        print(json.dumps(retrieve(args.query, project, args.k), ensure_ascii=False, indent=2))
        return 0
    if not 1 <= args.k <= MAX_K:
        parser.error(f"--k must be between 1 and {MAX_K}")
    out = prepare(args.query, project, args.state, args.k, external=args.external, record=bool(args.record))
    tape = out.pop("tape", None)
    if tape is not None:
        args.record.write_text(json.dumps(tape, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
