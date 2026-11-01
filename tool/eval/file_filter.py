"""`python tool/eval/file_filter.py --replay N [--out FILE]`

The `work.files` saving, measured: the N most recently merged specs, each
replayed through work start's file filter with it off (every candidate
loaded) and on (only the files Jev kept), from the revision the spec started
on. Per spec: files offered, kept and dropped, the first turn's input
tokens both ways — the session's system prompt, `Start.` and the file
section — and the Jev request's usage.

Sends one Jev request per spec and, with `ANTHROPIC_API_KEY` set, both first
turns to Anthropic's token-counting endpoint (counted, never answered);
without that key the count is characters / 4 and says so. No turn goes to a
coding session. Writes `eval/jev/file-filter/results/<date>.json`, kept off git.

Limits: search hits come from today's index, filtered to files that existed
at the starting revision; evidence the work-start choice may attach goes to
both arms alike and is left out of both.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common import settings  # noqa: E402
from main import channels, file_filter, specs  # noqa: E402
from main.query import ROOT  # noqa: E402
from session_state import run as git  # noqa: E402

RESULT = "jev-file-filter-replay/1"
OUT = ROOT / "eval" / "jev" / "file-filter" / "results"
COUNTER = "https://api.anthropic.com/v1/messages/count_tokens"
COUNTED_BY = "claude-opus-5-5"


def latest(n: int) -> list[tuple[dict, Path, str]]:
    """`(spec, repository, starting revision)` for the `n` most recently
    merged specs whose repository and merge commit are still here."""

    found = []
    for folder in specs.SPECS.iterdir() if specs.SPECS.is_dir() else []:
        repo = channels.repo_for(folder.name)
        if repo is None:
            continue
        for s in file_filter.merged(folder.name):
            commit = s["merge"]["commit"]
            start = s.get("start_head") if s.get("start_head") and git(repo, "cat-file", "-t", s["start_head"]) \
                else git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^1")
            if start:
                found.append((s, repo, start))
    return sorted(found, key=lambda f: file_filter.merged_at(f[0]), reverse=True)[:n]


def started_at(spec: dict) -> float:
    return min((h.get("ts") or 0 for h in spec.get("history") or [] if h.get("state") == "작업 중"),
               default=file_filter.merged_at(spec))


def count(system: str, texts: list[str]) -> tuple[list[int], str]:
    """Each first turn's input tokens, all counted the same way, and how: one
    arm the tokenizer could not count puts every arm on the estimate, so a
    saving never compares two methods."""

    key = settings.pick(settings.entries(ROOT / ".env") if (ROOT / ".env").exists() else {}, "ANTHROPIC_API_KEY")[0]
    counted = []
    for text in texts if key else []:
        body = json.dumps({"model": COUNTED_BY, "system": system,
                           "messages": [{"role": "user", "content": text}]}).encode("utf-8")
        req = urllib.request.Request(COUNTER, body, {"x-api-key": key, "anthropic-version": "2023-06-01",
                                                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as got:
                counted.append(int(json.loads(got.read())["input_tokens"]))
        except (OSError, ValueError, KeyError):
            break
    if key and len(counted) == len(texts):
        return counted, f"count_tokens:{COUNTED_BY}"
    return [(len(system) + len(t)) // 4 for t in texts], "estimate:chars/4"


def replay(spec: dict, repo: Path, start: str, cfg: decision.Config) -> dict:
    tree = file_filter.Tree(repo, start)
    offered = file_filter.candidates(spec, repo, tree, before=started_at(spec))
    rec = file_filter.judge(spec, offered, cfg, occasion="replay")
    system = specs.SPEC_PROMPT.rstrip() + "\n\n" + specs.brief(spec)
    every = [c["path"] for c in offered]
    (before, after), how = count(system, ["Start." + file_filter.render(every, [], tree),
                                          "Start." + file_filter.render([f["path"] for f in rec["kept"]],
                                                                        [f["path"] for f in rec["dropped"]], tree)])
    return {"repo": spec["repo"], "spec": spec["id"], "start": start, "status": rec["status"], "reason": rec["reason"],
            "threshold": rec["threshold"], "offered": offered, "kept": rec["kept"], "dropped": rec["dropped"],
            "tokens_before": before, "tokens_after": after, "counted": how,
            "jev": rec["jev"] and {k: rec["jev"][k] for k in ("status", "usage", "elapsed_ms", "model")}}


def table(rows: list[dict]) -> str:
    lines = ["| Spec | Offered | Kept | Dropped | First turn before | After | Saved | Jev in/out tokens |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for r in rows:
        usage = ((r["jev"] or {}).get("usage")) or {}
        saved = r["tokens_before"] - r["tokens_after"]
        lines.append(f"| {r['repo']}/{r['spec']}{'' if r['status'] == 'filtered' else ' (' + (r['reason'] or r['status']) + ')'} "
                     f"| {len(r['offered'])} | {len(r['kept'])} | {len(r['dropped'])} | {r['tokens_before']:,} "
                     f"| {r['tokens_after']:,} | {saved:,} | "
                     f"{usage.get('input_tokens', '–')}/{usage.get('output_tokens', '–')} |")
    before, after = sum(r["tokens_before"] for r in rows), sum(r["tokens_after"] for r in rows)
    lines.append(f"| **total** | {sum(len(r['offered']) for r in rows)} | {sum(len(r['kept']) for r in rows)} "
                 f"| {sum(len(r['dropped']) for r in rows)} | {before:,} | {after:,} | {before - after:,} | |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/file_filter.py", description="Replay the file filter")
    parser.add_argument("--replay", type=int, required=True, metavar="N")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = [replay(s, repo, start, decision.config(repo)) for s, repo, start in latest(args.replay)]
    out = args.out or OUT / f"{dt.date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": RESULT, "prompt_version": file_filter.VERSION, "specs": rows,
              "limits": {"lines": file_filter.LINES, "chars": file_filter.CHARS, "total": file_filter.TOTAL,
                         "max_files": file_filter.decisions.MAX_FILES}}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(table(rows))
    print(f"\n{out}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
