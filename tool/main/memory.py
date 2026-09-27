"""memory — what a cleared conversation leaves: nothing, or a pair in the
repository's `.wiki/memory/`.

`<stamp>-<focus>.raw.md` is the transcript as it was. `<stamp>-<focus>.md` is
the memory one fresh turn writes from it under `tool/prompts/chat-memory.md`,
checked by `shape` before it is written. The transcript is written first, so a
summary that fails loses nothing.
"""

from __future__ import annotations

import itertools
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from agent import oneshot

# The context row a clear leaves. What a screen shows starts after the last one.
CLEARED = "사용자가 문맥 지우기"

SAID = ("user", "assistant", "result")
LISTS = ("decisions", "facts", "preferences", "open", "references", "keywords")
WHO = {"user": "사람", "assistant": "답", "result": "결과"}


# Every append to a record and every clear of one. A clear reads the file and
# replaces it; an append in between — a spec's result lands in `next` from a
# loop's thread, holding nothing else — was lost.
_writing = threading.Lock()


def cleared(row: dict) -> bool:
    return row.get("role") == "context" and row.get("text") == CLEARED


def since_clear(rows: list[dict]) -> list[dict]:
    last = max((i for i, r in enumerate(rows) if cleared(r)), default=-1)
    return rows[last + 1:]


def append(file: Path, row: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    with _writing, file.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def clear(file: Path, owns: Callable[[dict], bool], marker: dict, delete: bool) -> list[dict]:
    """End the conversation `owns` picks out of `file`: its rows since its last
    clear are returned, `marker` goes after them, and with `delete` those rows
    leave the file. One read and one write under the lock appends take, so
    nothing lands in between; rows go by their place, so an identical row in
    another conversation or an earlier one stays."""

    with _writing:
        lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
        mine = []
        for i, line in enumerate(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and owns(row):
                if cleared(row):
                    mine.clear()
                else:
                    mine.append((i, row))
        doomed = {i for i, _ in mine} if delete else set()
        kept = [line for i, line in enumerate(lines) if i not in doomed]
        kept.append(json.dumps(marker, ensure_ascii=False))
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        temporary.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
        temporary.replace(file)
    return [row for _, row in mine]


def shape(text: str) -> dict:
    """The model's answer as the prompt's object. `ValueError` when it is not one."""

    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    data = json.loads(fenced.group(1) if fenced else body)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아니다")
    out = {}
    for key in ("title", "summary"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"`{key}` 가 없다")
        out[key] = " ".join(value.split()) if key == "title" else value.strip()
    for key in LISTS:
        value = data.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"`{key}` 가 목록이 아니다")
        if key == "decisions":
            out[key] = [{k: str(d.get(k) or "").strip() for k in ("what", "why", "rejected")}
                        for d in value if isinstance(d, dict) and str(d.get("what") or "").strip()]
        else:
            out[key] = [str(x).strip() for x in value if str(x).strip()]
    return out


def front(meta: dict) -> str:
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n"


def verification(row: dict) -> str | None:
    """What an answer's text was checked as (stage 7 of `docs/plans/jev/`):
    `verified:<status>`, or `unverified` — an answer published without a
    check, and every answer older than the check, whose plain text says
    nothing of it. `None` for what is not an answer."""

    if row.get("role") != "assistant":
        return None
    checked = row.get("verification")
    if isinstance(checked, dict) and checked.get("verified") is True and not checked.get("degraded"):
        return f"verified:{checked.get('status')}"
    return "unverified"


def transcript(rows: list[dict], meta: dict) -> str:
    turns = [f"## {WHO[r['role']]} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('ts', 0)))}"
             f"{f' · {verification(r)}' if verification(r) else ''}\n\n{r['text'].strip()}\n" for r in rows]
    return front(meta) + "\n" + "\n".join(turns)


def page(memory: dict, meta: dict) -> str:
    decisions = [d["what"] + (f" — {d['why']}" if d["why"] else "")
                 + (f" (rejected: {d['rejected']})" if d["rejected"] else "") for d in memory["decisions"]]
    parts = [front({**meta, "title": memory["title"], "keywords": memory["keywords"]}),
             f"# {memory['title']}\n", memory["summary"] + "\n"]
    for name, items in (("Decisions", decisions), ("Facts", memory["facts"]),
                        ("Preferences", memory["preferences"]), ("Open", memory["open"]),
                        ("References", memory["references"])):
        if items:
            parts.append(f"## {name}\n\n" + "".join(f"- {x}\n" for x in items))
    return "\n".join(parts)


def keep(repo: Path, focus: str, rows: list[dict], model: str = "", effort: str = "") -> dict:
    """Write the pair for `rows` and return their paths from `repo`. Nothing
    said means nothing written. `RuntimeError`, saying what is where, when
    either file could not be made — the rows themselves stay in the record
    (`raw/`), which a kept clear never deletes from."""

    said = [r for r in rows if r.get("role") in SAID and str(r.get("text") or "").strip()]
    if not said:
        return {}
    meta = {"repo": repo.name, "focus": focus, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    where = lambda p: p.relative_to(repo).as_posix()  # noqa: E731
    try:
        raw, mine = claim(repo / ".wiki" / "memory",
                          f"{time.strftime('%Y-%m-%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9_-]+', '-', focus)}")
        raw.write_text(transcript(said, {"kind": "transcript", **meta, "memory": mine.name}),
                       encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RuntimeError(f"원시 대화를 쓰지 못했다 — {exc}. 대화는 서버의 기록(raw/)에 그대로 있다") from exc
    try:
        answer = ""
        turns = [{"role": r["role"], "text": r["text"],
                  **({"verification": verification(r)} if verification(r) else {})} for r in said]
        for ev in oneshot("chat-memory.md", {"repo": repo.name, "focus": focus, "transcript": turns},
                          model, effort):
            if ev.kind == "error":
                raise RuntimeError(ev.text)
            if ev.kind == "done":
                answer = ev.text
        mine.write_text(page(shape(answer), {"kind": "memory", **meta, "raw": raw.name}),
                        encoding="utf-8", newline="\n")
    except (RuntimeError, ValueError, OSError) as exc:
        raise RuntimeError(f"원시 대화는 {where(raw)} 에 남겼다. 메모리는 못 만들었다 — {exc}") from exc
    return {"raw": where(raw), "memory": where(mine)}


def claim(folder: Path, stem: str) -> tuple[Path, Path]:
    """A transcript and memory pair of names no other clear holds. The
    transcript is created exclusively, so two clears in one second — the
    stamp's grain — get `-2` rather than overwriting each other."""

    folder.mkdir(parents=True, exist_ok=True)
    for n in itertools.count(1):
        name = stem if n == 1 else f"{stem}-{n}"
        try:
            (folder / f"{name}.raw.md").open("x").close()
        except FileExistsError:
            continue
        return folder / f"{name}.raw.md", folder / f"{name}.md"
    raise AssertionError("unreachable")
