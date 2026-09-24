"""transcript — lift out of a session log only what a retro needs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from workspace.sessions import INJECTED, MAX_HUMAN_CHARS, SESSIONS, logs  # noqa: E402

MAX_TURN_CHARS = 600   # How much of one utterance to show. A long paste may be cut


def human_text(record: dict) -> str | None:
    """The same rule as `census.human_turns`. `None` if a person did not type it."""

    if record.get("type") != "user":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        body = content
    elif isinstance(content, list):
        if not content or any(
            not isinstance(b, dict) or b.get("type") != "text" for b in content
        ):
            return None
        body = "\n".join(str(b.get("text") or "") for b in content)
    else:
        return None
    body = body.strip()
    if not body or len(body) > MAX_HUMAN_CHARS:
        return None
    if any(mark in body for mark in INJECTED):
        return None
    return body


def tool_names(record: dict) -> list[str]:
    if record.get("type") != "assistant":
        return []
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [str(b.get("name") or "?") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def read(path: Path) -> dict:
    turns: list[tuple[int, str, str]] = []
    tools: Counter[str] = Counter()
    for order, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        text = human_text(record)
        if text is not None:
            turns.append((order, str(record.get("timestamp") or "")[11:16], text))
        tools.update(tool_names(record))
    return {"file": path.name, "turns": turns, "tools": tools,
            "size_kb": path.stat().st_size // 1024}


def pick(files: list[Path], since: dt.datetime, session: str | None) -> list[Path]:
    """Narrow this checkout's logs to the ones asked for.

    Which files are this checkout's is settled before this — one log directory
    can hold two of them — so nothing here looks at a directory.
    """

    if session:
        return sorted(p for p in files if p.name.startswith(session))
    stamp = since.timestamp()
    return sorted((p for p in files if p.stat().st_mtime >= stamp),
                  key=lambda p: p.stat().st_mtime)


def render(sessions: list[dict]) -> str:
    if not sessions:
        return "해당하는 세션 로그가 없다."
    out = [f"세션 {len(sessions)}개 · 사람 발화 {sum(len(s['turns']) for s in sessions)}건 · "
           f"도구 호출 {sum(sum(s['tools'].values()) for s in sessions)}회", ""]
    for s in sessions:
        top = ", ".join(f"{k}×{v}" for k, v in s["tools"].most_common(5))
        out.append(f"## {s['file']} ({s['size_kb']}KB) — 발화 {len(s['turns'])} · 도구 {top or '없음'}")
        for order, at, text in s["turns"]:
            body = " ".join(text.split())
            if len(body) > MAX_TURN_CHARS:
                body = body[:MAX_TURN_CHARS] + f" …(+{len(body) - MAX_TURN_CHARS}자)"
            out.append(f"- [{at}#{order}] {body}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--since", default="midnight", help="YYYY-MM-DD 또는 midnight")
    ap.add_argument("--session", default=None, help="세션 id 앞부분")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.since == "midnight":
        since = dt.datetime.combine(dt.date.today(), dt.time.min)
    else:
        since = dt.datetime.fromisoformat(args.since)

    mine = logs(args.project.expanduser().resolve(), SESSIONS)
    sessions = [read(p) for p in pick(mine, since, args.session)]

    if args.json:
        for s in sessions:
            s["tools"] = dict(s["tools"])
        json.dump(sessions, sys.stdout, ensure_ascii=False)
    else:
        print(render(sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
