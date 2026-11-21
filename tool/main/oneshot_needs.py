"""What the owner gives an outside call: checked on the design, kept where each kind belongs, and every one given
before R1 may be approved.

`docs/plans/oneshot/11-real-outside.md` ("11a1") is the design. A `secret` lives in Windows Credential Manager under
`wiki-agent/oneshot/<repo>/<env>`, never in the spec, the record, an answer or an error; it stays there for the
repository's next run until the owner removes it, which they can at any stage, since the store itself lists what
it holds (`kept`). The other kinds are kept in the spec's `oneshot.given` with the `what` they were given for, so a
redesign that changes what a need is asks for it again. Giving or removing a need names the sheet it was made on.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import oneshot, oneshot_brief as brief, oneshot_sheet as sheet, release
from .oneshot_spec import VIEWS, idle, saved, spec_of, view

router = APIRouter()
NID = re.compile(r"N[1-9]\d*")
ENV = re.compile(r"[A-Z][A-Z0-9_]*")
KINDS = {"secret": "비밀 값", "account": "계정", "tool": "프로그램", "file": "파일"}
MAX_SECRET = 2560   # bytes Credential Manager holds in one credential, as UTF-16


def parsed(value, where: str) -> list[dict]:
    """One outside call's `needs`, shape-checked; a call from before them needs nothing."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where}: `needs` 는 목록이어야 한다")
    out = []
    for n in value:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not NID.fullmatch(n["id"]):
            raise ValueError(f"{where}: `needs` 의 `id` 는 N1, N2 … 여야 한다")
        if n.get("kind") not in KINDS:
            raise ValueError(f"{where} {n['id']}: `kind` 는 {', '.join(KINDS)} 중 하나다")
        if not isinstance(n.get("what"), str) or not n["what"].strip():
            raise ValueError(f"{where} {n['id']}: `what` 이 비었다")
        env = n.get("env")
        if n["kind"] == "secret" and not (isinstance(env, str) and ENV.fullmatch(env)):
            raise ValueError(f"{where} {n['id']}: 비밀 값은 `env` 에 대문자 환경 변수 이름을 적는다")
        if n["kind"] != "secret" and env is not None:
            raise ValueError(f"{where} {n['id']}: `env` 는 비밀 값에만 붙는다")
        out.append({"id": n["id"], "what": " ".join(n["what"].split()), "kind": n["kind"], **({"env": env} if env else {})})
    return out


def owed(design: dict) -> dict[str, tuple[dict, list[dict]]]:
    """Each need once, `{id: (need, the calls that need it)}`. Two calls may share a need by giving it the same
    id and the same fields; one id for two different needs, or one `env` under two ids, is refused."""

    out: dict[str, tuple[dict, list[dict]]] = {}
    envs: dict[str, str] = {}
    for x in design.get("outside") or []:
        for n in x.get("needs") or []:
            if n["id"] in out and out[n["id"]][0] != n:
                raise ValueError(f"{n['id']}: 같은 id 가 서로 다른 것을 가리킨다 — 다른 것이면 다른 id 를 쓴다")
            if n.get("env") and envs.setdefault(n["env"], n["id"]) != n["id"]:
                raise ValueError(f"{envs[n['env']]}, {n['id']}: 같은 `{n['env']}` 다 — 같은 값이면 한 id 로 합친다")
            out.setdefault(n["id"], (n, []))[1].append(x)
    return out


def section(design: dict) -> list[str]:
    """The sheet's `주인이 줄 것` section. The sheet is also the committed design document, so it never says what is given."""

    if not (needs := owed(design)):
        return []
    lines = ["", "## 주인이 줄 것", "", "| id | 줄 것 | 종류 | 쓰는 호출 | 그 호출의 요구 |", "| --- | --- | --- | --- | --- |"]
    for nid, (n, calls) in needs.items():
        reqs = sorted({d for x in calls for d in x["depends_on"] if sheet.RID.fullmatch(d)}, key=lambda r: int(r[1:]))
        kind = KINDS[n["kind"]] + (f" `{n['env']}`" if n.get("env") else "")
        lines.append(f"| {nid} | {sheet._cell(n['what'])} | {kind} | {', '.join(x['id'] for x in calls)} | "
                     f"{', '.join(reqs) or '없음'} |")
    return lines


def name(repo: str, env: str) -> str:
    return f"wiki-agent/oneshot/{repo}/{env}"


def held(repo: str, n: dict, kept: dict | None) -> bool:
    if n["kind"] == "secret":
        try:
            return release.secret_read(name(repo, n["env"])) is not None
        except (OSError, ValueError):   # a store that cannot be read, or none on this host, holds nothing
            return False
    if not kept or kept.get("what") != n["what"] or kept.get("kind") != n["kind"]:
        return False
    return n["kind"] == "account" or Path(kept["path"]).is_file()


def shown(spec: dict) -> list[dict]:
    """Every need with the calls that need it and whether it is given, for the screen; never a secret's value."""

    o = spec["oneshot"]
    kept = o.get("given") or {}
    return [{**n, "calls": [x["id"] for x in calls], "given": held(spec["repo"], n, kept.get(nid))}
            for nid, (n, calls) in owed(o.get("design") or {}).items()]


def missing(spec: dict) -> list[dict]:
    return [n for n in shown(spec) if not n["given"]]


def kept(repo: str) -> list[str]:
    """The `env` names Credential Manager holds a secret of this repository under, whatever the design or the stage
    says now: a redesign that renames or drops a need leaves its secret there, and the owner can still remove it."""

    try:
        ctypes, a = release._advapi()
    except ValueError:   # no Credential Manager on this host
        return []
    prefix, count = name(repo, ""), ctypes.c_uint32()
    found = ctypes.POINTER(ctypes.POINTER(release._Credential))()
    if not a.CredEnumerateW(prefix + "*", 0, ctypes.byref(count), ctypes.byref(found)):
        return []   # none stored (`ERROR_NOT_FOUND`), or a store that cannot be listed
    try:
        return sorted(found[i].contents.TargetName[len(prefix):] for i in range(count.value))
    finally:
        a.CredFree(found)


VIEWS.append(lambda spec: {"needs": shown(spec), "secrets_kept": kept(spec["repo"])} if spec["oneshot"].get("design")
             else {})


class Given(BaseModel):
    value: str = ""
    confirmed: bool = False
    source: str = ""   # the R1 sheet the owner gave it on (`oneshot_brief.source`); any other is refused with 409


def need_of(repo: str, nid: str, source: str) -> tuple[dict, dict]:
    """`(the spec's oneshot, the need)` while the run waits at R1 with no turn running, on the sheet the owner saw:
    after a redesign the same id may name another need, and a value meant for the old one never reaches it."""

    spec = spec_of(repo)
    o = spec["oneshot"]
    idle(o, "r1")
    if source != brief.source(brief.snapshot(spec, "r1")):
        raise HTTPException(409, "시트가 그 사이 바뀌었다 — 다시 불러와 새 시트에서 넣어라")
    if not (found := owed(o["design"]).get(nid)):
        raise HTTPException(404, f"설계에 없는 것이다: {nid}")
    return o, found[0]


def stored(repo: str, env: str, value: str | None) -> None:
    """A secret written, or removed with `None`; a failure names the store, never the value."""

    try:
        release.secret_write(name(repo, env), value) if value is not None else release.secret_delete(name(repo, env))
    except ValueError:
        raise HTTPException(409, "이 컴퓨터에는 Windows 자격 증명 관리자가 없다 — 비밀 값은 Windows 에서 넣는다") from None
    except OSError as e:
        raise HTTPException(409, f"자격 증명 관리자를 고치지 못했다 — {e}") from None


@router.post("/api/oneshot/spec/{repo}/needs/{nid}")
def give(repo: str, nid: str, body: Given) -> dict:
    """The owner gives one need. A secret goes to Credential Manager and nowhere else; no answer or error repeats
    what was typed."""

    with oneshot._lock:
        o, n = need_of(repo, nid, body.source)
        value = body.value.strip()
        if n["kind"] == "secret":
            if not value or len(value.encode("utf-16-le")) > MAX_SECRET:
                raise HTTPException(400, f"값이 비었거나 너무 길다 — 자격 증명 관리자는 {MAX_SECRET}바이트까지 담는다")
            stored(repo, n["env"], value)
            return view(spec_of(repo))
        if n["kind"] == "account":
            if not body.confirmed:
                raise HTTPException(400, "계정이 있다고 확인해야 한다")
            kept = {}
        elif n["kind"] == "tool":
            if not (found := shutil.which(value) if value else None):
                raise HTTPException(400, "그 프로그램을 PATH 에서 찾지 못했다 — 설치한 뒤 다시 넣어라")
            kept = {"path": str(Path(found).resolve())}
        else:
            if not value or not Path(value).is_absolute() or not Path(value).is_file():
                raise HTTPException(400, "그 경로에 파일이 없다 — 파일의 절대 경로를 넣어라")
            kept = {"path": str(Path(value).resolve())}
        saved(repo, oneshot={"given": {**(o.get("given") or {}), nid: {"what": n["what"], "kind": n["kind"], **kept}}})
    return view(spec_of(repo))


@router.delete("/api/oneshot/spec/{repo}/needs/{nid}")
def take_back(repo: str, nid: str, source: str = "") -> dict:
    """The owner removes what they gave on the sheet they saw: a secret from Credential Manager, the rest from the
    spec."""

    with oneshot._lock:
        o, n = need_of(repo, nid, source)
        if n["kind"] == "secret":
            stored(repo, n["env"], None)
        else:
            saved(repo, oneshot={"given": {k: v for k, v in (o.get("given") or {}).items() if k != nid}})
    return view(spec_of(repo))


@router.delete("/api/oneshot/spec/{repo}/secrets/{env}")
def forget(repo: str, env: str) -> dict:
    """A secret of this repository removed by its `env`, at any stage: one a redesign renamed or dropped
    included."""

    if not ENV.fullmatch(env):
        raise HTTPException(400, "`env` 는 대문자 환경 변수 이름이다")
    with oneshot._lock:
        spec = spec_of(repo)   # a repository with no oneshot spec here is refused before the store is touched
        stored(spec["repo"], env, None)
    return view(spec)
