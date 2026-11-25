"""Claude PreToolUse: a command its timeout moved to the background is waited on or stopped before other work.

A Bash call that outlives its timeout keeps running as a background task. The
agent did not ask for that, so nothing makes it decide what happens next: on
2026-11-25 a build turn's `du` over a uv cache was moved, the turn went on to
edit and test, and the walk ran on with nobody reading it. Here the session's
own transcript says which commands were moved (`backgroundTaskId` with
`timedOutAfterMs`) and which of them the agent has since waited on, read, or
stopped, or which ended. While one has none of these and is still running,
only the tools that decide it go through. Nothing here bounds how long any
wait is.

The transcript is read once from its start and then only past where the last
call stopped (`Scan`, kept in a file of its own), so a moved command is never
lost however long the session grows. A decision whose own result came back as
an error decides nothing. A command still counts as running only while its
output file is held open: one a process that has since ended left behind, as
a resumed session's are, holds nothing. Only Windows shows a held file, so
elsewhere nothing is held.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DECIDING = frozenset({"Yield", "Monitor", "TaskStop", "TaskOutput", "Read"})
WRITTEN_TO = re.compile(r"Output is being written to: (.+?\.output)")
STATE = Path(tempfile.gettempdir()) / "wiki-agent-background-left"
LIVENESS = os.name == "nt"   # where a running command's held output file can be seen (`running`)
REASON = """명령 {ids} 이(가) 시간 제한을 넘어 백그라운드로 넘어갔는데, 아직 기다리지도 멈추지도 않았다.
이대로 다음 일로 넘어가면 아무도 결과를 읽지 않는 채로 계속 돈다.

결과가 필요하면 Yield 로 기다리거나 출력 파일을 Read 한다. 필요 없으면 TaskStop 으로 멈춘다.
그 뒤에 이 호출을 다시 한다."""


@dataclass
class Scan:
    """What a transcript's first `offset` bytes say. `mark` is the file's identity and hashes of its first 4 KB
    and of the 4 KB just before `offset`, so a transcript replaced or rewritten is read again from its start.
    ponytail: a rewrite in place to the same size that keeps both windows goes unseen, since proving the whole
    prefix means reading it on every call. Claude only appends, and the worst such a rewrite does is miss a
    hold, never add a false one, since a stale task's output file has no writer. `tasks`: each moved task not
    yet over, by id, with its
    output file ("" when unnamed), whether a deciding call on it succeeded, and how many deciding calls on it
    still wait for their result. `deciding`: those calls, by tool use id, with the tasks each one names."""

    offset: int = 0
    mark: str = ""
    tasks: dict[str, dict] = field(default_factory=dict)
    deciding: dict[str, list[str]] = field(default_factory=dict)

    def undecided(self) -> dict[str, str]:
        """Tasks no deciding call has succeeded on or is still waiting on, with their output files."""

        return {t: s["output"] for t, s in self.tasks.items() if not s["decided"] and not s["open"]}

    def read(self, line: str) -> None:
        for task in [t for t in self.tasks if f"<task-id>{t}</task-id>" in line]:
            del self.tasks[task]   # its notice: the task is over, whatever a call on it still says
        if '"backgroundTaskId"' not in line and '"tool_use' not in line:
            return
        try:
            entry = json.loads(line)
        except ValueError:
            return
        result = entry.get("toolUseResult")
        moved = str(result["backgroundTaskId"]) if isinstance(result, dict) and result.get("backgroundTaskId") \
            and result.get("timedOutAfterMs") else ""
        output = ""
        content = (entry.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and entry.get("type") == "assistant":
                self.decide(block)
            elif block.get("type") == "tool_result":
                if moved and (named := WRITTEN_TO.search(_text(block.get("content")))):
                    output = named.group(1)
                for task in self.deciding.pop(str(block.get("tool_use_id")), []):
                    if state := self.tasks.get(task):
                        state["open"] -= 1
                        # A call that failed or was refused decided nothing; one that went through decided it.
                        state["decided"] = state["decided"] or not block.get("is_error")
        if moved:
            self.tasks[moved] = {"output": output, "decided": False, "open": 0}

    def decide(self, block: dict) -> None:
        name, given = block.get("name"), json.dumps(block.get("input") or {})
        if name not in DECIDING:
            return
        named = [t for t, s in self.tasks.items()
                 if not s["decided"] and (name in ("Yield", "Monitor") or t in given)]   # a wait covers all
        for task in named:
            self.tasks[task]["open"] += 1
        if named:
            self.deciding[str(block.get("id"))] = named


def _text(content) -> str:
    """A tool result's text, given as a string or as text blocks."""

    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return str(content or "")


def _mark(fh, offset: int) -> str:
    """The open transcript's identity, and hashes of its first 4 KB and of the 4 KB before `offset`: to tell
    the history a scan read from another written at the same path."""

    stat = os.fstat(fh.fileno())
    fh.seek(0)
    head = hashlib.sha256(fh.read(min(4096, offset))).hexdigest()
    fh.seek(max(0, offset - 4096))
    tail = hashlib.sha256(fh.read(offset - max(0, offset - 4096))).hexdigest()
    return f"{stat.st_dev}:{stat.st_ino}:{head}:{tail}"


def _saved(transcript: Path) -> Path:
    return STATE / (hashlib.sha256(str(transcript.resolve()).encode("utf-8")).hexdigest()[:32] + ".json")


def scanned(transcript: Path) -> Scan:
    """The transcript read through its last complete line, from where the saved scan stopped."""

    saved, size = _saved(transcript), transcript.stat().st_size
    try:
        scan = Scan(**json.loads(saved.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        scan = Scan()
    with transcript.open("rb") as fh:
        if scan.offset > size or _mark(fh, scan.offset) != scan.mark:   # rewritten: another history
            scan = Scan(mark=_mark(fh, 0))
        fh.seek(scan.offset)
        data = fh.read()
        end = data.rfind(b"\n") + 1   # a line still being written waits for the next call
        for line in data[:end].decode("utf-8", "replace").splitlines():
            scan.read(line)
        scan.offset += end
        scan.mark = _mark(fh, scan.offset)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        part = saved.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps(asdict(scan)), encoding="utf-8")
        os.replace(part, saved)
    except OSError:
        pass   # the next call reads from the start again
    return scan


def running(output: str) -> bool:
    """Whether a task's output file is shown held open for writing by the command writing it; a reader that
    opened it to look does not count. Only Windows share modes show
    that: elsewhere, or for an unnamed file, nothing is shown, and nothing is held, since a hold for a task whose
    process may have ended (a resumed session's) would stop the session on a guess."""

    if not output or not LIVENESS:
        return False
    if not os.path.exists(output):
        return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    # Read access, sharing read only: an open that has write access conflicts, a reader does not.
    handle = k32.CreateFileW(output, 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() == 32   # ERROR_SHARING_VIOLATION: a writer holds it
    k32.CloseHandle(handle)
    return False


def left(transcript: Path) -> list[str]:
    """Tasks a timeout moved to the background, still running, that the session has neither waited on, read,
    nor stopped, oldest first."""

    return [task for task, output in scanned(transcript).undecided().items() if running(output)]


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
