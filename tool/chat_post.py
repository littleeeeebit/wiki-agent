"""chat_post — put text written elsewhere into a channel."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "raw" / "chat"


def post(channel: str, text: str, source: str = "", *, project: Path) -> None:
    from main.channels import BY_ID
    if channel not in BY_ID:
        raise ValueError("그런 채널이 없다")
    project = project.expanduser().resolve()
    if not (project / ".git").exists():
        raise ValueError(f"저장소가 없다: {project}")
    LOGS.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), "role": "assistant", "text": text.strip(), "repo": str(project)}
    if source:
        row["source"] = source
    with (LOGS / f"{channel}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--source", default="", help="standup · retro 같은 출처 표시")
    ap.add_argument("--file", type=Path, default=None, help="없으면 stdin")
    args = ap.parse_args()

    text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
    if not text.strip():
        print("빈 본문", file=sys.stderr)
        return 1
    post(args.channel, text, args.source, project=args.project)
    print(f"#{args.channel} 에 넣었다 ({len(text)}자)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
