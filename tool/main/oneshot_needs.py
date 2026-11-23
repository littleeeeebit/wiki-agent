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
import itertools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import errorlog, folded, masked, overlaps, unescaped

from . import oneshot, oneshot_brief as brief, oneshot_sheet as sheet, release
from .oneshot_spec import VIEWS, idle, saved, spec_of, view

router = APIRouter()
NID = re.compile(r"N[1-9]\d*")
ENV = re.compile(r"[A-Z][A-Z0-9_]*")
# What the agent's launch sets or drops (`common.host.environment`, `agent.deepseek.environment`), and what an
# agent CLI reads as its own login: a need of that name would never reach the agent as given.
LAUNCH = re.compile(r"WIKI_AGENT_.*|ORCA_.*|RELEASE_KEY_PASSWORD|ANTHROPIC_.*|CLAUDE_.*|DEEPSEEK_API_KEY|"
                    r"OPENAI_API_KEY|CODEX_.*")
# What a look sets when it starts the product (`oneshot_look.served`, `oneshot_desktop.LOOK_ENV` and `OWNED_ENV`): a
# need of that name would reach the started app as the look's value, never the owner's.
STARTED = re.compile(r"PORT|APP_DATA_DIR|PYTHONUNBUFFERED|ONESHOT_SCREEN|ONESHOT_STATE|APPDATA|LOCALAPPDATA|TEMP|TMP")
WEB_STARTED = frozenset({"PORT", "APP_DATA_DIR", "PYTHONUNBUFFERED"})   # what `oneshot_look.served` sets itself
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
        if n["kind"] == "secret" and LAUNCH.fullmatch(env):
            raise ValueError(f"{where} {n['id']}: `{env}` 는 서버가 에이전트를 띄울 때 정하는 이름이다 — 다른 이름을 적는다")
        if n["kind"] == "secret" and STARTED.fullmatch(env):
            raise ValueError(f"{where} {n['id']}: `{env}` 는 화면 검사가 앱을 띄울 때 정하는 이름이다 — 다른 이름을 적는다")
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
    return [{**n, "calls": [x["id"] for x in calls], "given": held(spec["repo"], n, kept.get(nid)),
             "leaked": nid in marked(o), "unsure": nid in unsure(o) - marked(o),
             "launch": bool(LAUNCH.fullmatch(n.get("env") or "")),
             "started": [sheet.KIND_NAMES[k] for k in kept_from(spec, n.get("env") or "")]}
            for nid, (n, calls) in owed(o.get("design") or {}).items()]


def missing(spec: dict) -> list[dict]:
    return [n for n in shown(spec) if not n["given"]]


def unready(spec: dict) -> str:
    """Why R1 is not approved yet: something the owner gives is not given, or a need bears a launch name
    (`launchable`), which an edit at R1 renames."""
    if lacking := missing(spec):
        return "주인이 줄 것이 아직 없다 — " + ", ".join(f"{x['id']} {x['what']}" for x in lacking)
    return launchable(spec)


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
    forms = {nid: {value, quote(value, safe="")} for nid, n in secret_needs(spec) if (value := have.get(n["env"].upper()))}
    every = [form for kept in forms.values() for form in kept]
    return {form: label(nid, every, i) for i, (nid, kept) in enumerate(forms.items()) for form in kept}


class Label(str):
    """A need's mask as shown, carrying the need's id: the label need not spell it (`label`)."""

    def __new__(cls, shown: str, need: str):
        made = super().__new__(cls, shown)
        made.need = need
        return made


def label(nid: str, values: list[str], n: int) -> str:
    """`[N1]` for need `N1`, or the first other spelling none of `values` meets (`common.overlaps`), so no value
    shows through a label and masking twice is masking once, for a value given now or kept from before. At last, a
    run of a character no value holds, one longer for each need."""

    for left, right in ("[]", "⟦⟧", "‹›", "«»"):
        if not any(overlaps(v, [shown := f"{left}{nid}{right}"]) for v in values):
            return Label(shown, nid)
    run = next(c for c in map(chr, itertools.count(0x25A0)) if not any(folded(c) in folded(v) for v in values))
    return Label(run * (n + 2), nid)


def launchable(spec: dict) -> str:
    """Why R1 is not approved: a design saved before `LAUNCH` names what the agent's launch sets, drops or reads as
    its own login. The card stays at R1, where an edit renames it. One approved before keeps running: that need
    never reaches the build cell (`agent`), only the gate, and its row says so (`shown`)."""
    if bad := sorted({n["env"] for _, n in secret_needs(spec) if LAUNCH.fullmatch(n["env"])}):
        return (f"비밀 값 이름 {', '.join(f'`{b}`' for b in bad)} 은(는) 에이전트를 띄울 때 서버가 정하는 이름이다 "
                "— 그 항목을 수정 의견으로 다른 이름으로 바꾼 뒤 승인한다")
    if bad := sorted({n["env"] for _, n in secret_needs(spec) if STARTED.fullmatch(n["env"])}):
        return (f"비밀 값 이름 {', '.join(f'`{b}`' for b in bad)} 은(는) 화면 검사가 앱을 띄울 때 정하는 이름이다 "
                "— 그 항목을 수정 의견으로 다른 이름으로 바꾼 뒤 승인한다")
    return ""


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
    # A launch name (`LAUNCH`) never reaches the agent: the launch overrides it or reads it as its own login.
    env = {n["env"]: given.get(n["env"]) for _, n in needs if not LAUNCH.fullmatch(n["env"])}
    return env, hidden(spec, given)   # every given value masked, a launch name's too: the gate's output carries it


def leaked(path: Path, spec: dict, hide: dict | None, final: str, last: bool = False) -> str:
    """Why a build turn is refused when what it leaves holds a secret its session carried (`hide`, from `agent`):
    its answer, every commit since the design commit with its message and names (the history the pull request
    pushes), or what it has not committed. Matched in any casing; the refusal names the need, never the value, and
    a failed read names only what failed: a Git or file error can quote what it read."""

    # Whatever the store holds now, a rotated value is still in those commits; the scan below still runs, so a
    # commit made meanwhile is recorded too.
    if not hide:   # nothing carried, nothing found now: the sheet keeps only what the history still holds
        if spec_of(spec["repo"])["oneshot"].get("leaked"):
            saved(spec["repo"], oneshot={"leaked": []})
        return still_tainted(path, spec_of(spec["repo"]))
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
        places = {"답": decoded(final), "커밋": decoded(log + git("diff", "HEAD")),
                  "커밋하지 않은 파일": decoded("\n".join(map(read, untracked)))}
    except (OSError, subprocess.SubprocessError) as exc:
        return unscanned(path, spec, hide, f"{step} 이(가) 실패했다 ({type(exc).__name__})")
    found: dict[str, set[str]] = {}
    for where, text in places.items():
        flat = folded(text)
        for value, label in hide.items():
            if value in text or folded(value) in flat:
                found.setdefault(where, set()).add(label)
    marked = sorted(set().union(*map(need_ids, found.values())))
    if marked != (spec_of(spec["repo"])["oneshot"].get("leaked") or []):   # the sheet marks each need found now
        saved(spec["repo"], oneshot={"leaked": marked})
    if "커밋" in found and base:   # the commits that hold it stay refused until the history no longer has them
        try:
            hit = {c: ids for c in git("rev-list", f"{base}..HEAD").split() if (ids := held_in(git("show", *HISTORY, c), hide))}
        except (OSError, subprocess.SubprocessError) as exc:
            return unscanned(path, spec, hide, f"{step} 이(가) 실패했다 ({type(exc).__name__})")
        o = spec_of(spec["repo"])["oneshot"]   # each commit with every need it was ever found holding
        old = o.get("tainted_needs") or {}
        saved(spec["repo"], oneshot={"tainted": sorted({*(o.get("tainted") or []), *hit}), "tainted_needs": {
            **old, **{c: sorted({*old.get(c, []), *ids}) for c, ids in hit.items()}}})
    # A hold with these very values is covered by this finished scan; only a turn's last scan (`settle`) ends it.
    d = digest(spec, hide) if (holds := spec_of(spec["repo"])["oneshot"].get("unscanned") or {}) else None
    if last and d in holds:
        saved(spec["repo"], oneshot={"unscanned": {k: v for k, v in holds.items() if k != d}})
    refused = still_tainted(path, spec_of(spec["repo"]), covered=d)
    if not found:
        return refused
    every, rows = list(hide), list(owed(spec["oneshot"].get("design") or {}).items())

    def ref(label: str) -> str:
        """The need as the sheet names it, each one unique there: the first of `[id]`, its variable and its row
        (`시트의 k번째 항목`, the sheet's order) that no value meets (`overlaps` reads across whatever text is around
        it), so no mask of this text or of a prompt around it rewrites the reference. Its label when a value meets
        all three; the sheet marks every need found (`leaked`), so even that one is found there."""
        nid = getattr(label, "need", None) or str(label).strip("[]")
        k, n = next(((k, n) for k, (i, (n, _)) in enumerate(rows, 1) if i == nid), (0, {}))
        return next((r for r in (f"[{nid}]", n.get("env"), k and f"시트의 {k}번째 항목") if isinstance(r, str) and r
                     and not any(overlaps(v, [r]) for v in every)), str(label))

    ids = sorted(ref(lab) for lab in set().union(*found.values()))
    return " / ".join(filter(None, [refused, (
        f"비밀 값 {', '.join(ids)} 이(가) {', '.join(found)} 에 들어 있다 (시트에 표시) — 값은 환경 변수로만 읽고 어디에도 적지 "
        "않는다. 커밋에 들어갔다면 설계 커밋 뒤의 그 커밋들을 다시 써서 기록에서도 지운다")]))


# A commit's names, message and patch; a merge's against its first parent, so a resolution's own text is read.
HISTORY = ("-p", "--diff-merges=first-parent", "--format=%an%n%ae%n%cn%n%ce%n%B")


def need_ids(labels) -> set[str]:
    return {getattr(lab, "need", None) or str(lab).strip("[]") for lab in labels}


def decoded(text: str) -> str:
    """`text`, then the same text as a reader of its JSON gets it (`common.unescaped`): what any block, fence or
    parser sends or saves is the decoded value, which a JSON escape (`\\u0041`, `\\/`) spells differently."""
    return f"{text}\n{unescaped(text)}"


def carried(texts: list[str], values: list[str], hide: dict) -> list[str]:
    """The labels of `hide` whose value a screen's `texts` (its drawn and carried text, each one stream) or `values`
    (its fields' contents) hold: as spelled, or in any casing a style can draw (`folded`, whose fold of a character
    can depend on the one before); within one text, a value broken across lines reads as one."""

    def compact(text: str) -> str:
        return "".join(text.split())

    def flats(form, text: str) -> set[str]:   # whitespace gone before and after the spelling, since a fold can
        return {form(compact(text)), compact(form(text))}   # depend on the character a line break stood next to

    spellings = [(form, [form(x) for x in texts + values], [f for t in texts for f in flats(form, t)])
                 for form in (str, folded)]

    def seen(value: str) -> bool:
        for form, whole, streams in spellings:
            v = form(value)
            if v and any(v in t for t in whole):
                return True
            if any(f and any(f in s for s in streams) for f in flats(form, value)):
                return True
        return False

    return sorted({label for value, label in hide.items() if seen(value)})


def kept_from(spec: dict, env: str) -> list[str]:
    """The run's kinds whose look sets `env` itself when it starts the app (a `STARTED` name only a design approved
    before the rule can bear), so that start runs without the owner's value; the Android file carries every name."""

    from . import oneshot_desktop   # it reaches the stages that import this module
    from .oneshot_profile import kinds_of

    own = {"webapp": WEB_STARTED, "desktop": oneshot_desktop.LOOK_ENV | oneshot_desktop.OWNED_ENV}
    return [k for k in kinds_of(spec["oneshot"]) if env.upper() in own.get(k, ())]


def given_in(spec: dict, env: dict | None, own: frozenset[str] | set[str] = frozenset()) -> dict[str, str | None]:
    """What a started product gets over the server's environment: each secret the design names by its `env`, its
    value in `env` (a `gate_env` result), or `None` (dropped) for one `env` does not hold, so a variable of that name
    the server happens to have never stands in for it. A name in `own`, one the launcher sets itself (a `STARTED`
    name, which only a design approved before the rule can bear), is left out: the launcher's value wins, and the
    need's row and the build prompt say that start runs without it (`shown`, `told`)."""

    have = {k.upper(): v for k, v in (env or {}).items()}
    return {n["env"]: have.get(n["env"].upper()) for _, n in secret_needs(spec) if n["env"].upper() not in own}


def held_in(text: str, hide: dict) -> list[str]:
    """The needs whose value `text` holds, in any casing, as spelled or as its JSON decodes (`decoded`), by id."""
    text = decoded(text)
    flat = folded(text)
    return sorted(need_ids(label for value, label in hide.items() if value in text or folded(value) in flat))


def marked(o: dict) -> set[str]:
    """The needs the sheet marks: those a turn's answer, commits or files held when last checked (`leaked`), and those
    a commit still in the history holds (`tainted_needs`), so the mark and the history refusal clear together."""
    return {*(o.get("leaked") or []), *(n for c in o.get("tainted") or [] for n in (o.get("tainted_needs") or {}).get(c, []))}


def unsure(o: dict) -> set[str]:
    """The needs a scan that could not finish carried (`unscanned`, `failed`): not found, awaiting a scan."""
    return {n for h in (o.get("unscanned") or {}).values() if h.get("failed") for n in h["needs"]}


def owe(path: Path, run) -> None:
    """A turn carrying a oneshot's secrets owes a scan from its start: a hold for its set of values is written
    before anything is sent, so a turn that never reaches its last scan (`settle`), a restart included, still
    holds what it may have made. Raises, and the turn does not start, when the hold cannot be written."""

    from . import specs   # it imports this module

    if (hide := getattr(run.chat, "hide", None)) and (spec := specs.owner(path)) and spec.get("oneshot"):
        holds, d = spec_of(spec["repo"])["oneshot"].get("unscanned") or {}, digest(spec, hide)
        if d not in holds:
            saved(spec["repo"], oneshot={"unscanned": {**holds, d: {"needs": sorted(need_ids(hide.values())), "failed": False}}})


def settle(path: Path, run, final: str, moving: bool = False) -> bool:
    """The end of every turn on a oneshot's worktree, while it still holds it, after the stage check and whatever
    that check wrote or committed: the tree as it is released is scanned under the turn's own mask, or reconciled
    without one, however the turn ended. Never raises; a scan that broke leaves the hold a failed read leaves
    (`unscanned`, kept in memory when even that write fails). A refusal of a turn whose stage passed and was
    `moving` the run on stops it with the reason, shown on the run and resumable once fixed, and returns `False`:
    nothing that was to follow starts. A refused turn's repair turn goes on; it is scanned again before its check."""

    from . import oneshot_spec, specs   # they import this module

    hide, spec, moving = getattr(getattr(run, "chat", None), "hide", None), None, moving and getattr(run, "passed", False)
    try:
        if not ((spec := specs.owner(path)) and spec.get("oneshot")):
            return True
        why = leaked(path, spec, hide, final, last=True)
    except Exception as exc:
        errorlog.record("oneshot-scan", type(exc).__name__, path=str(path))
        try:
            why = unscanned(path, spec, hide, type(exc).__name__) if hide and spec else f"비밀 값 검사가 실패했다 ({type(exc).__name__})"
        except Exception as again:
            errorlog.record("oneshot-scan", type(again).__name__, path=str(path))
            why = "비밀 값 검사를 끝내지 못했다 — 턴이 시작할 때 적어 둔 보류가 게시와 다음 단계를 막는다"
    if why and moving and spec:
        try:
            oneshot_spec.store(spec["repo"]).stop("block", masked(why, hide or {}))
        except Exception as exc:
            errorlog.record("oneshot-scan", type(exc).__name__, path=str(path))
    return not (why and moving)


# The UTF-16 byte orders an archive's text may take; a value in any of them, or in UTF-8, is found.
ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be")


def asset(file: Path, spec: dict, hide: dict | None = None) -> str:
    """Why an asset about to be uploaded (the frozen APK or installer) is not sent: a value of `hide` (the store's
    now, by default) anywhere in it, as UTF-8 or either UTF-16, in any casing: the raw file, every byte the upload
    sends; an APK being a zip, each entry decompressed with its name; and an NSIS installer, its whole solid
    stream unpacked, the script and every file it installs (`release_desktop.unpacked`), read in overlapping
    windows. A file that cannot be read is refused too."""

    import zipfile
    import zlib

    from . import release_desktop

    hide = agent(spec)[1] if hide is None else hide
    if not hide:
        return ""

    def held(b: bytes) -> set[str]:   # UTF-16 pairs may start at an odd byte: read from the first and the second
        return {n for enc in ENCODINGS for s in (b, b[1:]) for n in held_in(s.decode(enc, "ignore"), hide)}

    try:
        parts, packed = [file.read_bytes()], False   # every byte that goes up, signing blocks and gaps included
        if zipfile.is_zipfile(file):
            with zipfile.ZipFile(file) as z:   # and what the entries hold compressed
                parts += [b for i in z.infolist() for b in (i.filename.encode(), z.read(i))]
        else:
            packed = release_desktop.MAGIC in parts[0]
        found = set().union(*map(held, parts))
        if packed:   # a value split across two chunks is whole in the window that carries the last one's tail
            reach, tail = 12 * max(map(len, hide)) + 16, b""   # a JSON `\uXXXX` escape in UTF-16 is 12 bytes a character
            for chunk in release_desktop.unpacked(parts[0]):
                found |= held(tail + chunk)
                tail = (tail + chunk)[-reach:]
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, zlib.error, EOFError) as exc:
        return masked(f"올릴 파일을 읽지 못해 올리지 않는다 ({type(exc).__name__})", hide)
    found = sorted(found)
    return masked(f"비밀 값 {', '.join(f'[{n}]' for n in found)} 이(가) 올릴 파일에 들어 있다 — 올리지 않는다", hide) \
        if found else ""


def publishable(path: Path, spec: dict) -> str:
    """Why nothing goes out: checked at each external write (the APK upload, the push and the pull request) on the
    exact state it sends, under the values the store holds now, with every refusal and hold recorded before. A
    check that cannot finish refuses too."""

    hide: dict = {}
    try:
        hide = agent(spec)[1]
        return masked(leaked(path, spec, hide, ""), hide)   # as the stop that keeps it shows it
    except Exception as exc:
        errorlog.record("oneshot-scan", type(exc).__name__, path=str(path))
        return masked(f"비밀 값 검사를 끝내지 못해 게시하지 않는다 ({type(exc).__name__})", hide)


def digest(spec: dict, hide: dict) -> str:
    """The values a session carried, as a slow salted digest (`rotated`'s), never a value."""
    return hashlib.pbkdf2_hmac("sha256", json.dumps(sorted(hide)).encode(), str(spec["oneshot"].get("run")).encode(),
                               200_000).hex()


def unscanned(path: Path, spec: dict, hide: dict, what: str) -> str:
    """A scan that could not finish marks its turn's hold (`unscanned`, written at the turn's start by `owe`) failed:
    refused and shown as unsure until a turn's last scan with the same values finishes (`leaked`), or the history
    after the design commit is gone and nothing is left uncommitted (`still_tainted`); a value replaced meanwhile
    can no longer be looked for."""
    # One hold per set of values, each owed alone; the turn's start wrote it (`owe`), this marks it failed.
    holds = spec_of(spec["repo"])["oneshot"].get("unscanned") or {}
    saved(spec["repo"], oneshot={"unscanned": {**holds, digest(spec, hide): {
        "needs": sorted(need_ids(hide.values())), "failed": True}}})
    return " / ".join(filter(None, [still_tainted(path, spec_of(spec["repo"])),
                                    f"비밀 값이 남았는지 확인하지 못했다 — {what}"]))


def still_tainted(path: Path, spec: dict, covered: str | None = None) -> str:
    """Why a turn is refused while a commit once found holding a secret is still in HEAD's history, or empty,
    clearing the record once none is. Bound to the commits, never to today's values, so a value the owner
    replaced or took back is refused until the history is rewritten. A scan left owed (`unscanned`) is refused
    the same way, until the history after the design commit is gone and nothing is left uncommitted."""

    def empty(*args: str) -> bool:   # a read that failed is never empty
        done = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        return done.returncode == 0 and not done.stdout.strip()

    # `covered`: the hold of the values a scan just finished with, which that scan answers for, kept until its turn's
    # last scan ends it.
    holds, base = spec["oneshot"].get("unscanned") or {}, spec["oneshot"].get("design_head")
    hold = {k: v for k, v in holds.items() if k != covered}
    if hold and base and empty("rev-list", f"{base}..HEAD") and empty("status", "--porcelain"):
        saved(spec["repo"], oneshot={"unscanned": {k: v for k, v in holds.items() if k == covered}})
        hold = {}
    unsure = "비밀 값 검사를 끝내지 못한 턴이 있다 — 같은 값으로 다시 검사하거나 설계 커밋 뒤의 기록을 지운다" if hold else ""
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
    if left != tainted:   # only the commits Git says are gone leave the record, with the needs they held
        saved(spec["repo"], oneshot={"tainted": left, "tainted_needs": {
            c: n for c, n in (spec["oneshot"].get("tainted_needs") or {}).items() if c in left}})
    return " / ".join(filter(None, [unsure, (f"비밀 값이 들어 있던 커밋 {', '.join(c[:12] for c in left)} 이(가) 아직 "
                                           "기록에 있다 — 설계 커밋 뒤의 그 커밋들을 다시 써서 기록에서 지운다") if left else ""]))


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
        if n["kind"] == "secret" and LAUNCH.fullmatch(n["env"]):   # never in the agent's shell (`agent`)
            lines.append(f"- `{n['env']}`: {nid} {n['what']}, a secret the server's gate alone has: it is not in "
                         f"your shell, so do not set, read or repair it there; the gate runs its live check ({uses})")
        elif n["kind"] == "secret" and (kept := kept_from(spec, n["env"])):   # a look's own value wins (`given_in`)
            lines.append(f"- `{n['env']}`: {nid} {n['what']}, a secret the gate has, but the {' and '.join(kept)} "
                         f"screen check sets `{n['env']}` itself, so the app runs there without it ({uses})")
        elif n["kind"] == "secret":
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
