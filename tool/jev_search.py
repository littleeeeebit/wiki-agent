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

Stage 9 — the app's runs, from here. `--answer` runs the whole question as
the app's active mode does: retrieval, a draft by a read-only host session
in the project, verification and publication, in one `knowledge.Run` whose
trace lands where the app's do; prints its `run-summary/1`. `--run <id>`
prints a stored run's summary, and `--export <id>` its summary and events
with every text a source or a person wrote left out (`--with-text` keeps
them). The effective mode and where it came from go to stderr first, so a
command line never disagrees with the app silently.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import decision  # noqa: E402
from main import knowledge  # noqa: E402
from main.knowledge import MAX_K, prepare, replay, retrieve  # noqa: E402


def answer(question: str, project: str | None, state: str, k: int, model: str) -> dict:
    """One question to its publication, as the app's active mode runs it —
    `prepare` and `grounded` in a `Run` — with a host session drafting.
    The caller has checked the mode is active."""

    from agent import ChatSession
    from main import channels

    repo = Path(project) if project else knowledge.HUB
    cfg = decision.config(repo)
    run = knowledge.Run(repo, "cli", question, cfg)
    out, outcome, reason = None, "failed", None
    chat = ChatSession(repo, tools="Read,Glob,Grep", system=channels.ANSWER_PROMPT, model=model or None,
                       isolated=True)

    lead = [question]   # the first turn carries the question, as the app's `drafting` sends it

    def generate(message: str):
        if lead:
            message = f"{lead.pop()}\n\n{message}"
        for ev in chat.say(message, run.cancel):
            if ev.kind == "error" or (ev.kind == "done" and ev.meta.get("error")):
                raise RuntimeError(ev.text or "the host turn failed")
            if ev.kind == "done":
                yield {**ev.meta, "kind": "usage"}   # this turn's model and tokens, for the trace
                return ev.text
        raise RuntimeError("the host turn ended without an answer")
        yield  # a generator, as `knowledge.grounded` asks

    try:
        dossier = prepare(question, project, state, k, cfg=cfg, run=run)
        flow = knowledge.grounded(question, project, state, dossier, generate, cfg, run=run)
        while True:
            try:
                next(flow)
            except StopIteration as stop:
                out = stop.value
                break
        outcome = out["verified"]["status"]
    except Exception as exc:  # noqa: BLE001 — the run still ends, and says why
        reason = type(exc).__name__
        run.put({"kind": "error", "code": "internal", "text": str(exc)[:300]})
    finally:
        chat.close()
    summary = run.finish(outcome, reason, out, answered=out["text"] if out else "")
    knowledge.tracing.flush()   # a command line exits next: its trace is sent first
    return summary


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
    parser.add_argument("--answer", action="store_true", help="draft, verify and publish, as the app's active mode")
    parser.add_argument("--model", default="", help="the host model drafting an --answer")
    parser.add_argument("--run", metavar="ID", help="print a stored run's summary")
    parser.add_argument("--export", metavar="ID", help="print a stored run's summary and events, texts left out")
    parser.add_argument("--with-text", action="store_true", help="keep the texts in an --export")
    args = parser.parse_args(argv)
    project = str(Path(args.project).expanduser().resolve()) if args.project else None
    status = decision.config(project or knowledge.HUB).status()
    print(f"jev: mode {status['mode']} (from {status['mode_source']}), key {'set' if status['key'] else 'missing'}"
          f" ({status['key_source']}), model {status['model']}", file=sys.stderr)
    if args.run or args.export:
        root = project or str(knowledge.HUB)
        found = (knowledge.stored(args.run, root)[0] if args.run else
                 knowledge.export(args.export, root, text=args.with_text))
        if found is None:
            print(f"no run {args.run or args.export} in {root}", file=sys.stderr)
            return 1
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    if args.replay:
        out = replay(json.loads(args.replay.read_text(encoding="utf-8")))
        print(json.dumps({k: out[k] for k in ("matches", "prompt_changed", "transitions", "recorded")},
                         ensure_ascii=False, indent=2))
        return 0 if out["matches"] else 1
    if not args.query:
        parser.error("a query is required")
    if args.answer:
        if not 1 <= args.k <= MAX_K:
            parser.error(f"--k must be between 1 and {MAX_K}")
        if status["mode"] != "active":
            parser.error(f"--answer publishes only what was checked, which mode active does; the mode is "
                         f"{status['mode']} (from {status['mode_source']})")
        print(json.dumps(answer(args.query, project, args.state, args.k, args.model), ensure_ascii=False, indent=2))
        return 0
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
