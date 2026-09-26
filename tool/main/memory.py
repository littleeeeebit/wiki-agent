"""memory — what a cleared conversation leaves: nothing, or a pair in the
repository's `.wiki/memory/`.

`<stamp>-<focus>.raw.md` is the transcript as it was. `<stamp>-<focus>.md` is
the memory one fresh turn writes from it under `tool/prompts/chat-memory.md`,
checked by `shape` before it is written. The transcript is written first, so a
summary that fails loses nothing.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from agent import oneshot

# The context row a clear leaves. What a screen shows starts after the last one.
CLEARED = "사용자가 문맥 지우기"

SAID = ("user", "assistant", "result")
LISTS = ("decisions", "facts", "preferences", "open", "references", "keywords")
WHO = {"user": "사람", "assistant": "답", "result": "결과"}


def since_clear(rows: list[dict]) -> list[dict]:
    last = max((i for i, r in enumerate(rows)
                if r.get("role") == "context" and r.get("text") == CLEARED), default=-1)
    return rows[last + 1:]


def drop(file: Path, rows: list[dict]) -> None:
    """Rewrite `file` without `rows`. Every other line stays as it was."""

    if not rows or not file.exists():
        return
    doomed = {json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows}
    kept = []
    for line in file.read_text(encoding="utf-8").splitlines():
        try:
            gone = json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True) in doomed
        except json.JSONDecodeError:
            gone = False
        if not gone:
            kept.append(line)
    temporary = file.with_suffix(".tmp")
    temporary.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
    temporary.replace(file)


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


def transcript(rows: list[dict], meta: dict) -> str:
    turns = [f"## {WHO[r['role']]} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('ts', 0)))}\n\n"
             f"{r['text'].strip()}\n" for r in rows]
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
    said means nothing written. `RuntimeError` when the memory could not be
    made; the transcript is on disk by then, and the message says where."""

    said = [r for r in rows if r.get("role") in SAID and str(r.get("text") or "").strip()]
    if not said:
        return {}
    folder = repo / ".wiki" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y-%m-%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9_-]+', '-', focus)}"
    raw, mine = folder / f"{stem}.raw.md", folder / f"{stem}.md"
    meta = {"repo": repo.name, "focus": focus, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    raw.write_text(transcript(said, {"kind": "transcript", **meta, "memory": mine.name}),
                   encoding="utf-8", newline="\n")
    where = lambda p: p.relative_to(repo).as_posix()  # noqa: E731
    try:
        answer = ""
        for ev in oneshot("chat-memory.md", {"repo": repo.name, "focus": focus,
                                             "transcript": [{"role": r["role"], "text": r["text"]} for r in said]},
                          model, effort):
            if ev.kind == "error":
                raise RuntimeError(ev.text)
            if ev.kind == "done":
                answer = ev.text
        memory = shape(answer)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(f"원시 대화는 {where(raw)} 에 남겼다. 메모리는 못 만들었다 — {exc}") from exc
    mine.write_text(page(memory, {"kind": "memory", **meta, "raw": raw.name}), encoding="utf-8", newline="\n")
    return {"raw": where(raw), "memory": where(mine)}
