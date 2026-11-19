"""oneshot S7's PC app build: the frontend build's `oneshot-desktop` block, the program its gate built, and that
program frozen into staging the run owns, with the identity every look launches.

`docs/plans/oneshot/9-kinds.md` ("The runnable program") is the design. `declared` reads the block's shape before
the gate; `built` checks what the gate left; `frozen` copies the build folder out of the worktree, records each
file's digest and the launch settings, and names the build by one digest over both. Any change to a file, an
argument, the working directory or a data setting is a new identity, so a look's pass counts only for the build
it launched.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from common import is_junction

from . import oneshot
from .oneshot_screens import MOCK_PATH

BLOCK = "oneshot-desktop"
DATA = "{data}"   # where a look puts the app's data: a fresh folder it owns, per attempt
PACKAGED = (".msix", ".msixbundle", ".appx", ".appxbundle", ".msi")
INSTALLERS = (".exe", ".msi", ".msix", ".msixbundle")
# An application manifest asking for more than the caller's rights, embedded as a resource or beside the program.
ELEVATION = re.compile(rb"requestedExecutionLevel[^>]*?level\s*=\s*[\"'](?:requireAdministrator|highestAvailable)",
                       re.IGNORECASE)
MAX_FILES, MAX_BYTES = 5000, 1 << 30   # a folder of what the launch loads, never a whole build tree
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _relative(value, where: str) -> str:
    if not isinstance(value, str) or not MOCK_PATH.fullmatch(value) \
            or {".", "..", ".git"} & {part.lower() for part in value.split("/")}:
        raise ValueError(f"`{BLOCK}` 의 {where} 는 상대 경로(`/` 로 나눔, `.`·`..` 없음)로 적는다 — {value!r}")
    return value


def declared(tree: Path, final: str) -> dict:
    """The build turn's `oneshot-desktop` block, checked for shape: `run`'s build folder, its program inside it,
    the arguments, the working directory inside that folder, the environment, with `{data}` named at least once
    and nothing naming the live worktree; and the installer S8 attaches. Whether the files exist is `built`'s,
    after the gate."""

    value = oneshot.block(final, BLOCK)
    if not isinstance(value, dict) or not isinstance(value.get("run"), dict):
        raise ValueError(f"`{BLOCK}` 는 run{{folder, program, args, cwd, env}} 과 installer 를 담은 객체다")
    run = value["run"]
    out = {"folder": _relative(run.get("folder"), "run.folder"), "program": _relative(run.get("program"), "run.program"),
           "args": run.get("args", []), "cwd": run.get("cwd", "."), "env": run.get("env", {})}
    if out["cwd"] != ".":
        _relative(out["cwd"], "run.cwd")
    if Path(out["program"]).suffix.lower() in PACKAGED or Path(out["program"]).suffix.lower() != ".exe":
        raise ValueError(f"`{BLOCK}` 의 run.program 은 패키지하지 않은 디버그 빌드의 `.exe` 다 — MSIX 나 설치 파일은 "
                         "띄우지 않는다")
    if not isinstance(out["args"], list) or not all(isinstance(a, str) for a in out["args"]):
        raise ValueError(f"`{BLOCK}` 의 run.args 는 문자열 목록이다")
    if not isinstance(out["env"], dict) or not all(isinstance(k, str) and ENV_NAME.fullmatch(k) and isinstance(v, str)
                                                    for k, v in out["env"].items()):
        raise ValueError(f"`{BLOCK}` 의 run.env 는 환경 변수 이름과 문자열 값의 객체다")
    settings = [*out["args"], *out["env"].values()]
    if not any(DATA in s for s in settings):
        raise ValueError(f"`{BLOCK}` 의 run 은 앱의 데이터를 `{DATA}` 에 두는 인자나 환경 변수를 하나 이상 적는다")
    live = str(tree.resolve()).replace("\\", "/").lower()
    if any(live in s.replace("\\", "/").lower() for s in settings):
        raise ValueError(f"`{BLOCK}` 의 run 설정이 작업트리를 가리킨다 — 실행은 얼린 사본과 `{DATA}` 만 읽는다")
    installer = _relative(value.get("installer"), "installer")
    if Path(installer).suffix.lower() not in INSTALLERS:
        raise ValueError(f"`{BLOCK}` 의 installer 는 S8 이 붙일 설치 파일(.exe, .msi, .msix)이다 — {installer}")
    if installer == f"{out['folder']}/{out['program']}":
        raise ValueError(f"`{BLOCK}` 의 installer 와 run.program 이 같은 파일이다 — run 은 설치하지 않고 띄우는 빌드다")
    return {"run": out, "installer": installer}


def _inside(tree: Path, relative: str) -> Path | None:
    """`relative` under `tree` when no step of it is a link and it resolves inside `tree`, else `None`."""

    parts = Path(relative).parts
    steps = [tree.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    if any(p.is_symlink() or is_junction(p) for p in steps) or not (tree / relative).resolve().is_relative_to(
            tree.resolve()):
        return None
    return tree / relative


def built(tree: Path, desktop: dict) -> str:
    """Why the program `run` names is not one the look may start, or empty: its folder and program are plain
    files inside the worktree after the gate, the folder holds no link and is not a packaged layout, the
    working directory is inside it, and no manifest asks for elevation."""

    run = desktop["run"]
    folder = _inside(tree, run["folder"])
    if folder is None or not folder.is_dir():
        return f"게이트 뒤에 `{run['folder']}` 가 작업트리 안의 폴더로 없다 — `gate_cmd` 가 그 빌드를 만들어야 한다"
    program = _inside(folder, run["program"])
    if program is None or not program.is_file():
        return f"게이트 뒤에 `{run['folder']}/{run['program']}` 가 그 폴더 안의 파일로 없다"
    if (folder / "AppxManifest.xml").exists():
        return f"`{run['folder']}` 는 MSIX 로 패키지한 배치다 — run 은 패키지하지 않은 디버그 빌드다"
    if run["cwd"] != "." and ((cwd := _inside(folder, run["cwd"])) is None or not cwd.is_dir()):
        return f"run.cwd `{run['cwd']}` 가 `{run['folder']}` 안의 폴더로 없다"
    sidecar = program.with_name(program.name + ".manifest")
    if ELEVATION.search(program.read_bytes()) or sidecar.is_file() and ELEVATION.search(sidecar.read_bytes()):
        return f"`{run['program']}` 의 매니페스트가 관리자 권한을 요구한다 — run 은 권한 상승 없이 뜨는 빌드다"
    return ""


def frozen(tree: Path, desktop: dict, into: Path) -> dict:
    """`run`'s build folder copied into `into`, a folder the run owns, with `manifest.json` beside the copy: each
    file's digest and the launch settings, the program and working directory as paths inside the copy. The
    build's identity is one digest over both. A folder holding a link, or more than a launch loads, is refused."""

    run, source = desktop["run"], tree / desktop["run"]["folder"]
    files, size, folders = {}, 0, [source]
    while folders:   # each entry checked before it is entered: a link is never followed, even to count it
        for path in sorted(folders.pop().iterdir()):
            if path.is_symlink() or is_junction(path):
                raise ValueError(f"`{run['folder']}` 에 링크가 있다 — `{path.relative_to(source).as_posix()}`")
            if path.is_dir():
                folders.append(path)
                continue
            size += path.stat().st_size
            files[path.relative_to(source).as_posix()] = None
            if len(files) > MAX_FILES or size > MAX_BYTES:
                raise ValueError(f"`{run['folder']}` 가 실행이 읽는 것보다 크다 (파일 {MAX_FILES}개, {MAX_BYTES >> 20} MB "
                                 "까지) — 실행에 필요한 파일만 모은 폴더를 `gate_cmd` 가 만든다")
    files = dict(sorted(files.items()))
    app = into / "app"
    shutil.copytree(source, app)
    for name in files:   # the copy's digests: what the look launches, whatever the worktree holds by then
        files[name] = hashlib.sha256((app / name).read_bytes()).hexdigest()
    launch = {"program": run["program"], "args": run["args"], "cwd": run["cwd"], "env": run["env"]}
    identity = hashlib.sha256(json.dumps({"files": files, "launch": launch}, sort_keys=True).encode()).hexdigest()
    (into / "manifest.json").write_text(json.dumps({"identity": identity, "files": files, "launch": launch},
                                                   indent=1, sort_keys=True), encoding="utf-8")
    return {"identity": identity, "folder": str(app), "files": len(files), "launch": launch,
            "installer": desktop["installer"]}
