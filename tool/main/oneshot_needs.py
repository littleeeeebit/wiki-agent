"""What the owner gives an outside call: checked on the design, kept where each kind belongs, and every one given
before R1 may be approved.

`docs/plans/oneshot/11-real-outside.md` ("11a1", "11b1") is the design. A `secret` lives in Windows Credential Manager under
`wiki-agent/oneshot/<repo>/<env>`, never in the spec, the record, an answer or an error; it stays there for the
repository's next run until the owner removes it, which they can at any stage, since the store itself lists what
it holds (`kept`). The other kinds are kept in the spec's `oneshot.given` with the `what` they were given for, so a
redesign that changes what a need is asks for it again. Giving or removing a need names the sheet it was made on.

The tests and the build are told every need (`told`), and every gate the server runs for the run has each given
secret in its environment under its `env` (`gate_env`); the value is read from the store for that gate only, and
the gate's output shows `[N1]` where it would stand (`hidden`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import folded

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


def leaving(design: dict, place: str, mark) -> list[str]:
    """The sheet's calls that leave `place`, then what the owner gives them."""

    if not (outside := design.get("outside")):
        return []
    cell = sheet._cell
    return ["", f"## {place} 밖으로 나가는 호출", "", "| id | 대상 | 하는 일 | 실패할 때 |", "| --- | --- | --- | --- |",
            *[f"| {x['id']}{mark(x['id'])} | {cell(x['target'])} | {cell(x['does'])} | {cell(x['failure'])} |"
              for x in outside], *section(design)]


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


def secret_needs(spec: dict) -> list[tuple[str, dict]]:
    return [(nid, n) for nid, (n, _) in owed(spec["oneshot"].get("design") or {}).items() if n["kind"] == "secret"]


def _given(spec: dict) -> list[tuple[str, str, str]]:
    """`(id, env, value)` for each given secret, read from the store for one use; a need not given is absent."""

    out = []
    for nid, n in secret_needs(spec):
        try:
            value = release.secret_read(name(spec["repo"], n["env"]))
        except (OSError, ValueError):   # no store on this host, or one that cannot be read: it is not given
            value = None
        if value:
            out.append((nid, n["env"], value))
    return out


def secrets(spec: dict) -> dict[str, str]:
    """Each given secret's value by its `env`."""

    return {env: value for _, env, value in _given(spec)}


def hidden(spec: dict, env: dict | None) -> dict[str, str]:
    """What a gate's output shows instead of each secret `env`, a `gate_env` result, carries: `[N1]` for its value
    and its URL-quoted form. Read off the environment the child gets, never the store again, so a secret changed or
    removed in between can never be the one launched and left unmasked."""

    have = {k.upper(): v for k, v in (env or {}).items()}
    # ponytail: the raw and URL-quoted forms only; another encoding of a value (base64, JSON-escaped) still shows
    return {form: f"[{nid}]" for nid, n in secret_needs(spec) if (value := have.get(n["env"].upper()))
            for form in {value, quote(value, safe="")}}


def gate_env(spec: dict, env: dict | None) -> dict | None:
    """`env`, the server's own when `None`, with every given secret set. A secret the design names but the store
    does not hold is removed, so a variable of that name the server happens to have never stands in for it."""

    if not (declared := {n["env"].upper() for _, n in secret_needs(spec)}):
        return env
    base = {k: v for k, v in (os.environ if env is None else env).items() if k.upper() not in declared}
    return {**base, **secrets(spec)}


def agent(spec: dict | None) -> tuple[dict, dict]:
    """A oneshot build session's environment over the server's, and what it shows instead of each value: every
    given secret by its `env`, and `None` (dropped) for one the design names but the store does not hold."""

    if not (spec or {}).get("oneshot") or not (needs := secret_needs(spec)):
        return {}, {}
    given = secrets(spec)
    env = {n["env"]: given.get(n["env"]) for _, n in needs}
    return env, hidden(spec, env)


def leaked(path: Path, spec: dict, hide: dict | None, final: str) -> str:
    """Why a build turn is refused when what it leaves holds a secret its session carried (`hide`, from `agent`):
    its answer, every commit since the design commit with its message and names (the history the pull request
    pushes), or what it has not committed. Matched in any casing; the refusal names the need, never the value, and
    a failed read names only what failed: a Git or file error can quote what it read."""

    # Whatever the store holds now, a rotated value is still in those commits; the scan below still runs, so a
    # commit made meanwhile is recorded too.
    if not hide:
        return still_tainted(path, spec)
    refused = still_tainted(path, spec)
    step = ""

    def git(*args: str) -> str:
        nonlocal step
        step = f"git {args[0]}"
        done = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        if done.returncode:
            raise OSError(done.returncode)
        return done.stdout

    def read(file: Path) -> str:
        nonlocal step
        step = "커밋하지 않은 파일 읽기"
        return file.read_bytes().decode("utf-8", "ignore") if file.is_file() else ""

    base = spec["oneshot"].get("design_head")
    try:   # a tree that cannot be read is refused, never taken as clean
        # ponytail: text diffs and untracked text only; a secret inside a committed binary is not seen
        untracked = [path / f for f in git("ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
        log = git("log", *HISTORY, f"{base}..HEAD") if base else ""
        places = {"답": final, "커밋": log + git("diff", "HEAD"), "커밋하지 않은 파일": "\n".join(map(read, untracked))}
    except (OSError, subprocess.SubprocessError) as exc:
        return refused or f"비밀 값이 남았는지 확인하지 못했다 — {step} 이(가) 실패했다 ({type(exc).__name__})"
    found: dict[str, set[str]] = {}
    for where, text in places.items():
        flat = folded(text)
        for value, label in hide.items():
            if value in text or folded(value) in flat:
                found.setdefault(where, set()).add(label.strip("[]"))
    if not found:
        return refused
    if "커밋" in found and base:   # the commits that hold it stay refused until the history no longer has them
        try:
            hit = [c for c in git("rev-list", f"{base}..HEAD").split() if seen(git("show", *HISTORY, c), hide)]
        except (OSError, subprocess.SubprocessError) as exc:
            return f"비밀 값이 남았는지 확인하지 못했다 — {step} 이(가) 실패했다 ({type(exc).__name__})"
        saved(spec["repo"], oneshot={"tainted": sorted({*(spec_of(spec["repo"])["oneshot"].get("tainted") or []), *hit})})
    ids = sorted(set().union(*found.values()))
    return " / ".join(filter(None, [refused, (
        f"비밀 값 {', '.join(ids)} 이(가) {', '.join(found)} 에 들어 있다 — 값은 환경 변수로만 읽고 어디에도 적지 "
        "않는다. 커밋에 들어갔다면 설계 커밋 뒤의 그 커밋들을 다시 써서 기록에서도 지운다")]))


# A commit's names, message and patch; a merge's against its first parent, so a resolution's own text is read.
HISTORY = ("-p", "--diff-merges=first-parent", "--format=%an%n%ae%n%cn%n%ce%n%B")


def seen(text: str, hide: dict) -> bool:
    flat = folded(text)
    return any(value in text or folded(value) in flat for value in hide)


def still_tainted(path: Path, spec: dict) -> str:
    """Why a turn is refused while a commit once found holding a secret is still in HEAD's history, or empty,
    clearing the record once none is. Bound to the commits, never to today's values, so a value the owner
    replaced or took back is refused until the history is rewritten."""

    tainted = spec["oneshot"].get("tainted") or []
    left = []
    for c in tainted:
        # Gone only when Git says so: not an ancestor (1), or no such commit (1 from `--verify --quiet`). Any other
        # answer, a read that failed included, keeps it.
        done = subprocess.run(["git", "merge-base", "--is-ancestor", c, "HEAD"], cwd=path, capture_output=True,
                              timeout=60)
        if done.returncode != 1 and subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}"],
                                                   cwd=path, capture_output=True, timeout=60).returncode != 1:
            left.append(c)
    if left != tainted:   # only the commits Git says are gone leave the record
        saved(spec["repo"], oneshot={"tainted": left})
    return (f"비밀 값이 들어 있던 커밋 {', '.join(c[:12] for c in left)} 이(가) 아직 기록에 있다 — 설계 커밋 뒤의 "
            "그 커밋들을 다시 써서 기록에서 지운다") if left else ""


def rotated(spec: dict | None, env: dict, rows: list[dict]) -> str | None:
    """Whether the secrets a oneshot session carries differ from those the worktree's conversation last ran with:
    it held those values, so it must not resume. The answer is written as a `context` row by the caller, which
    `resumable` never resumes past, so a restart before the next turn still starts afresh. The new digest is
    returned when it differs, else `None`: a slow salted digest, never a value. Nothing is saved here, so the
    session lock never waits on the spec's."""

    if not (spec or {}).get("oneshot"):
        return None
    data = json.dumps(sorted(env.items())).encode()
    digest = hashlib.pbkdf2_hmac("sha256", data, str(spec["oneshot"].get("run")).encode(), 200_000).hex() if env else ""
    return None if digest == next((r["secrets"] for r in reversed(rows) if "secrets" in r), "") else digest


def told(spec: dict) -> str:
    """The needs as the tests and the build read them: a secret by its variable, a program or a file by the path
    it was given at, an account by what it is. Never a secret's value."""

    given = spec["oneshot"].get("given") or {}
    lines = []
    for nid, (n, calls) in owed(spec["oneshot"].get("design") or {}).items():
        uses = ", ".join(x["id"] for x in calls)
        if n["kind"] == "secret":
            lines.append(f"- `{n['env']}`: {nid} {n['what']}, a secret ({uses})")
        elif n["kind"] in ("tool", "file") and (path := (given.get(nid) or {}).get("path")):
            lines.append(f"- {nid} {n['what']}, a {'program' if n['kind'] == 'tool' else 'file'} at `{path}` ({uses})")
        else:
            lines.append(f"- {nid} {n['what']}, an account the owner has ({uses})")
    return "\n".join(lines) or "- none: no outside call needs anything from the owner"


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
