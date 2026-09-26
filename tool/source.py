"""`python tool/source.py <command> [--project <repo>] ...` — a repository's external sources.

    list                          local sources by kind, external records by status
    url <url>                     fetch one explicit URL into research
    arxiv --query "<words>"       search arXiv; `--id 1706.03762` looks papers up
          [--n 5] [--full]        `--full` also reads the PDF
    file <path>                   register a local PDF, Markdown or text paper
    adopt <source> --rationale ... --claim ... [--claim ...] --scope ...
          [--counter ...] [--condition ...]
    reject <source> --rationale ... [--counter ...]
    enable <source> | disable <source> | remove <source>
    promote <source> [--task <name>]   commit the adopted source's page in a new worktree

A `<source>` is a record's id, or its first eight characters or more. Stage 3
of `docs/plans/jev/` (`main.knowledge`). Only what is named here is fetched:
no link inside a document, and no web search. arXiv is free and asks for one
request every three seconds, which the provider keeps.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import knowledge  # noqa: E402
from search import providers  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/source.py", description="External sources of a repository")
    parser.add_argument("--project", default=None, help="the target repository; the hub's own without one")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("url").add_argument("url")
    papers = commands.add_parser("arxiv")
    papers.add_argument("--query")
    papers.add_argument("--id", action="append", default=[])
    papers.add_argument("--n", type=int, default=5)
    papers.add_argument("--full", action="store_true")
    commands.add_parser("file").add_argument("path")
    for verdict in ("adopt", "reject"):
        decide = commands.add_parser(verdict)
        decide.add_argument("source")
        decide.add_argument("--rationale", required=True)
        decide.add_argument("--counter", action="append", default=[])
        if verdict == "adopt":
            decide.add_argument("--claim", action="append", required=True)
            decide.add_argument("--scope", required=True)
            decide.add_argument("--condition", action="append", default=[])
    for name in ("enable", "disable", "remove"):
        commands.add_parser(name).add_argument("source")
    promote = commands.add_parser("promote")
    promote.add_argument("source")
    promote.add_argument("--task")
    args = parser.parse_args(argv)
    project = str(Path(args.project).expanduser().resolve()) if args.project else None

    try:
        if args.command == "list":
            out = knowledge.catalog(project)
        elif args.command == "url":
            out = knowledge.add_url(project, args.url)
        elif args.command == "arxiv":
            if not args.query and not args.id:
                parser.error("arxiv needs --query or --id")
            out = knowledge.add_papers(project, args.query, args.id or None, args.n, args.full)
        elif args.command == "file":
            out = knowledge.add_file(project, args.path)
        elif args.command == "adopt":
            out = knowledge.decide(project, args.source, "adopted", rationale=args.rationale, claims=args.claim,
                                   scope=args.scope, counterevidence=args.counter, conditions=args.condition)
        elif args.command == "reject":
            out = knowledge.decide(project, args.source, "rejected", rationale=args.rationale,
                                   counterevidence=args.counter)
        elif args.command in ("enable", "disable"):
            out = knowledge.switch(project, args.source, args.command == "enable")
        elif args.command == "remove":
            out = {"removed": knowledge.forget(project, args.source)}
        else:
            if not project:
                parser.error("promote needs --project")
            out = knowledge.promote(project, args.source, args.task)
    except (ValueError, KeyError, FileExistsError, RuntimeError, providers.FetchError) as error:
        reason = getattr(error, "reason", "") or str(error).strip("'\"")
        print(json.dumps({"error": type(error).__name__, "reason": reason}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
