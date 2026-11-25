"""Claude PreToolUse: a command its timeout moved to the background is waited on or stopped before other work.

A Bash call that outlives its timeout keeps running as a background task. The
agent did not ask for that, so nothing makes it decide what happens next: on
2026-11-25 a build turn's `du` over a uv cache was moved, the turn went on to
edit and test, and the walk ran on with nobody reading it. Here the session's
own transcript says which commands were moved (`backgroundTaskId` with
`timedOutAfterMs`) and which of them the agent has since waited on, read, or
stopped, or which ended. While one has none of these, only the tools that
decide it go through. Nothing here bounds how long any wait is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TAIL = 2 << 20   # bytes of the transcript read: a moved command is decided within its next few calls
DECIDING = frozenset({"Yield", "Monitor", "TaskStop", "TaskOutput", "Read"})
REASON = """명령 {ids} 이(가) 시간 제한을 넘어 백그라운드로 넘어갔는데, 아직 기다리지도 멈추지도 않았다.
이대로 다음 일로 넘어가면 아무도 결과를 읽지 않는 채로 계속 돈다.

결과가 필요하면 Yield 로 기다리거나 출력 파일을 Read 한다. 필요 없으면 TaskStop 으로 멈춘다.
그 뒤에 이 호출을 다시 한다."""


def left(transcript: Path) -> list[str]:
    """Tasks a timeout moved to the background that the session has neither waited on, read, nor stopped, and
    that have not ended, oldest first."""

    with transcript.open("rb") as fh:
        fh.seek(max(0, transcript.stat().st_size - TAIL))
        lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
    pending: list[str] = []
    for line in lines:
        if pending and (ended := [t for t in pending if f"<task-id>{t}</task-id>" in line]):
            pending = [t for t in pending if t not in ended]   # its notice: the task is over
        if '"backgroundTaskId"' not in line and '"tool_use"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        result = entry.get("toolUseResult")
        if isinstance(result, dict) and result.get("backgroundTaskId") and result.get("timedOutAfterMs"):
            pending.append(str(result["backgroundTaskId"]))
            continue
        content = (entry.get("message") or {}).get("content") if entry.get("type") == "assistant" else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, given = block.get("name"), json.dumps(block.get("input") or {})
            if name in ("Yield", "Monitor"):
                pending = []   # waiting on whatever is running
            elif name in DECIDING:
                pending = [t for t in pending if t not in given]
    return pending


def verdict(payload: dict) -> dict | None:
    """A deny while a moved command is undecided and this call does not decide it; otherwise `None`."""

    if payload.get("tool_name") in DECIDING or not payload.get("transcript_path"):
        return None
    try:
        ids = left(Path(str(payload["transcript_path"])))
    except OSError:
        return None
    if not ids:
        return None
    output = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
              "permissionDecisionReason": REASON.format(ids=", ".join(ids))}
    return {"hookSpecificOutput": output, "systemMessage": f"백그라운드로 넘어간 명령 결정 필요: {', '.join(ids)}"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    given = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if answer := verdict(given if isinstance(given, dict) else {}):
        json.dump(answer, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001 -- `craft/hooks-fail-open`
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
