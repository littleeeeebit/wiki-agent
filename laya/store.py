"""Files that survive an interrupted write (`docs/research/laya-loop-recovery.md`).

Journals are append-only JSONL, one record per line, each written with its newline in one
write. A last line without its newline is a torn record that was never committed: `read`
leaves it out and the next `append` cuts it off (or ends it, when it is whole), as a
write-ahead log repairs its tail. A complete line that does not parse is corruption, not
an interruption, and is raised. Derived files (notebook pages, the index) are replaced
whole through a temporary file, never left half written.
"""

import json
import os
import tempfile
from pathlib import Path


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    *lines, tail = path.read_text(encoding="utf-8").split("\n")
    rows = [json.loads(line) for line in lines if line.strip()]
    if tail.strip():
        try:
            rows.append(json.loads(tail))   # whole but not ended: committed
        except ValueError:
            pass                            # torn: never committed
    return rows


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        with path.open("rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":   # rare: only after an interrupted write, so a full read is fine
                f.seek(0)
                data = f.read()
                cut = data.rfind(b"\n") + 1
                try:
                    json.loads(data[cut:])
                    f.write(b"\n")
                except ValueError:
                    f.truncate(cut)
    # ponytail: no fsync, so a process interruption is covered and a power loss is not
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
