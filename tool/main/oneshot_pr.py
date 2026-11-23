"""oneshot S8: the README turn, the final gate, and the first pull request, handed to Review Loop.

`docs/plans/oneshot/5-screen-pr.md` ("S8 — the first pull request") is the design. After R3's approval
one work turn writes the README (and, for a public repository, the CI workflow); the server checks it
and runs the adapter's whole gate; then the ordinary pull request path takes over: the worker's
`pr-summary` (`pr_text`), push and `gh pr create` as the person (`specs.written`, `specs.opened`). An
Android app's turn also writes the `[release]` table, and one unsigned release build checks it
(`release_checked`, `docs/plans/release/0-overview.md` step 7).
From `PR #n` on the spec follows the ordinary cycle, and the run keeps its summary.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import yaml

import android_owned
from common import errorlog, masked, shell
from wiki import slots_for

from . import github_release, oneshot_build, oneshot_calls, oneshot_device, oneshot_needs, oneshot_publish, oneshot_spec
from . import pr_text, release, release_desktop, specs
from .oneshot_profile import kinds_of
from .query import ROOT

README_PROMPT = (ROOT / "tool/prompts/oneshot-readme.md").read_text(encoding="utf-8")
ADAPTER = ".wiki/adapter.toml"
RELEASE_PROMPT = (ROOT / "tool/prompts/oneshot-readme-release.md").read_text(encoding="utf-8")
DESKTOP_RELEASE_PROMPT = (ROOT / "tool/prompts/oneshot-readme-release-desktop.md").read_text(encoding="utf-8")
# `version_source` as the Android table's prompt words it, for a PC app with no Android app to share it.
SOURCE_NOTE = """
- `version_source` holds the app's version, `0.1.0` until the first release:
  a `.json` file with a top-level `version`, or a `.toml` file with
  `version` or `[package] version`, such as `src-tauri/tauri.conf.json`.
"""
STAGE = "pr"
TRIES = 3   # the prerelease's create and each asset's upload, as GitHub's own CLI retried them
# The files S8 attaches, by the record key their frozen copy (`<key>_frozen`) and upload keep: the kind that owes
# one, and its name bare and with its object and subject particles.
ASSETS = {"apk": ("android", "APK", "APK 를", "APK 가"), "installer": ("desktop", "설치 파일", "설치 파일을", "설치 파일이")}
NO_INSTALLER = ("`[release.desktop]` 가 없다 — 설치 파일은 Tauri(NSIS) 앱의 정식 빌드 표로만 만든다. "
                "다른 스택의 PC 앱은 아직 설치 파일을 붙이지 않는다")
OS_NAMES = {"windows": "Windows", "macos": "macOS"}
WORKFLOWS = ".github/workflows/"
CI = f"{WORKFLOWS}ci.yml"
# The workflow's events. Written unquoted: `test_prerequisite_review` reads a quoted push as a git push.
TRIGGERS = set("push pull_request".split())
KIND_PROMPT_NAMES = {"webapp": "a web app", "android": "an Android app", "desktop": "a Windows app"}
# The public CI's step after the gate, written as is: the tests skipped for want of a need, owed to the local gate.
SKIPS = r"""- name: Tests owed to the local gate
  if: always()
  shell: python
  run: |
    import os, re
    from pathlib import Path
    from xml.etree import ElementTree
    root = Path(os.environ.get("GITHUB_WORKSPACE") or ".")   # the checkout, whatever this step's folder
    found = sorted(root.glob("**/{report}"))   # wherever the gate's command changed into
    cases = None
    if len(found) == 1:
        try:
            cases = list(ElementTree.parse(found[0]).getroot().iter("testcase"))
        except (OSError, ElementTree.ParseError):
            pass
    rows = []
    for case in cases or []:
        skip = case.find("skipped")
        why = "" if skip is None else (skip.get("message") or "") + " " + (skip.text or "")
        if ids := sorted(set(re.findall(r"(?<![A-Za-z0-9_])N[1-9][0-9]*(?![A-Za-z0-9_])", why))):
            name = f"{case.get('classname') or ''} {case.get('name') or ''}"
            name = " ".join(name.split())   # literal in a code span, whose fence outruns any backtick run in it
            ticks = "`" * (max((len(run) for run in re.findall("`+", name)), default=0) + 1)
            rows.append(f"- {ticks} {name} {ticks}: {', '.join(ids)}")
    lines = ["## 로컬 게이트가 맡은 테스트", ""]
    if len(found) > 1:
        lines.append("게이트 보고서로 보이는 파일이 여럿이라 어느 것이 이 게이트의 것인지 정할 수 없다: "
                     + ", ".join(f"`{p.relative_to(root).as_posix()}`" for p in found))
    elif cases is None:
        lines.append("JUnit 보고서(`{report}`)가 없어 건너뛴 테스트를 셀 수 없다.")
    elif not rows:
        lines.append("비밀 값이 없어 건너뛴 테스트는 없다.")
    else:
        lines += ["CI 에는 비밀 값이 없어 아래 테스트를 건너뛰었다. 주인의 PC 에서 도는 게이트가 실제 호출로 돌린다.",
                  "", *rows]
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
""".replace("{report}", oneshot_build.REPORT)
SKIP_STEP = yaml.safe_load(SKIPS)[0]


def readme_text(spec: dict) -> str:
    profile = spec["oneshot"]["profile"]
    now = profile["os_now"]
    both = [OS_NAMES[now]] + ([OS_NAMES["macos" if now == "windows" else "windows"]]
                              if profile.get("os_later") == "other" else [])
    public = profile.get("visibility") == "public" and profile.get("ci") == "github-actions"
    kinds = kinds_of(spec["oneshot"])
    android = "android" in kinds   # alone, or among several kinds, whose gate builds it on the core
    gate = slots_for(None, Path(spec["worktree"])).get("gate_cmd", "").strip()
    run = f"a step that `run`s exactly these lines, as the server's gate ran them:\n\n```\n{ci_gate(gate)}\n```\n\n"
    job = (f"a job on `{RUNNER}` that sets up a JDK (`actions/setup-java`) and the Android SDK "
           f"(`android-actions/setup-android`), then {run}" if android else f"a job on `{RUNNER}` with {run}")
    several = f"""
This repository holds {len(kinds)} kinds of the application, sharing one core:
{', '.join(KIND_PROMPT_NAMES[k] for k in kinds)}. Give each its own section, in that
order, with its own Setup and Run; Test is one, since the gate runs the core's
tests and every kind's build. For the web app, Run is the command that starts
it and where it is then served.
""" if len(kinds) > 1 else ""
    return (README_PROMPT.replace("{os}", " and ".join(both))
            .replace("{gate}", gate)
            .replace("{android}", several + (f"""
{"In the Android app's section," if several else 'This is an Android app.'} Setup names the JDK and the Android SDK the build
needs. Run is instead the command that builds the debug APK,
`{spec['oneshot']['device']['apk']}`, and the one that installs it on a phone
connected with USB debugging on: adb's install command with that APK's path.
""" + RELEASE_PROMPT.replace("{package}", spec["oneshot"]["device"]["package"]) if android else "") + (f"""
{"In the PC app's section," if several else 'This is a PC app (a Windows program).'} Setup names the tools its build
needs. Run is the command that builds it and the one that starts the program
the build leaves. Install says the pull request's draft prerelease carries the
installer its release build makes, and how to run it.
""" + DESKTOP_RELEASE_PROMPT.replace("{where}", "add this table to `.wiki/adapter.toml` beside `[release.android]`, in "
                                     "the same `[release]`" if android else
                                     "add these tables to `.wiki/adapter.toml` after the ones already there")
            .replace("{head}", "" if android else '[release]\nversion_source = "the one .json or .toml file whose '
                                                  "version is the app's\"\n\n")
            .replace("{source}", "" if android else SOURCE_NOTE)
            .replace("{installer}", (spec["oneshot"].get("desktop_build") or {}).get("installer") or "")
            + scope(spec["oneshot"]) if "desktop" in kinds else ""))
            .replace("{workflow}", f"Also write `.github/workflows/ci.yml`: `on: [push, pull_request]` and {job}"
                     "That job has no `if` or `needs`, and that step no `if` or `background`. "
                     "After that step, in the same job, add this step exactly as written; it lists in the run's "
                     f"summary the tests CI skipped for want of a secret:\n\n```yaml\n{SKIPS}```"
                     if public else "Write no CI workflow: this repository gets none, and the server refuses one."))


def begin(repo: str) -> None:
    """After R3's approval: the README turn."""

    oneshot_build.send(repo, STAGE, restart(oneshot_spec.spec_of(repo)))


def restart(spec: dict) -> str:
    """The README turn's text; a resumed S8 starts there again, so a pull request text asked before is dropped."""

    specs.update(spec["repo"], spec["id"], writing=None)
    unstaged(spec)   # a gate's copies a stop kept from going up: the final gate after this turn freezes its own
    return readme_text(spec)


def workflows(path: Path) -> dict[str, str]:
    """HEAD's files under `.github/workflows/`, each with its tree mode (`100644`, `120000` for a link)."""

    done = specs.sh(["git", "-c", "core.quotepath=off", "ls-tree", "-r", "-z", "HEAD", "--", WORKFLOWS], path)
    if done.returncode:
        raise ValueError(f"git ls-tree 실패 — {(done.stderr or '').strip()[-200:]}")
    return {name: meta.split()[0] for meta, _, name in (e.partition("\t") for e in done.stdout.split("\0") if e)}


# The gate was proven in this machine's shell (`shell.launch`): PowerShell on Windows, bash elsewhere, and its commands
# may be the platform's own (`gradlew.bat`, `xcrun`). The runner is of the same system, its default shell alike.
RUNNER = {"win32": "windows-latest", "darwin": "macos-latest"}.get(sys.platform, "ubuntu-latest")


def ci_gate(gate: str) -> str:
    """The gate as the server's shell ran it: PowerShell's call operator before a quoted program (`shell.invoked`)."""

    return shell.invoked(gate) if os.name == "nt" else gate


def ci_file(path: Path, mode: str | None, gate: str) -> bool:
    """`ci.yml` committed as a plain file (a link points anywhere) whose workflow has a trigger and a
    job step that runs the gate, followed in that job by `SKIPS`: a comment or an empty job is no CI."""

    if mode not in ("100644", "100755"):
        return False
    shown = specs.sh(["git", "show", f"HEAD:{CI}"], path)
    try:
        flow = yaml.safe_load(shown.stdout) if not shown.returncode else None
    except yaml.YAMLError:
        return False
    if not isinstance(flow, dict) or not isinstance(flow.get("jobs"), dict):
        return False
    on = flow.get("on", flow.get(True))   # YAML 1.1 reads the bare key `on` as true
    events = {on} if isinstance(on, str) else set(on) if isinstance(on, (list, dict)) else set()
    if not TRIGGERS <= events:   # on every push and pull request, not by hand only
        return False
    # The gate's lines run as a block of `run`, each exactly: named in a comment or an `echo` it never runs,
    # and an argument added (`--collect-only`, a narrower selection) runs something else. Arguments live in
    # `gate_cmd`, which may span lines (the front build extends the backend's).
    want = [line.strip() for line in ci_gate(gate).splitlines() if line.strip()]
    for job in flow["jobs"].values():
        # A condition, or a job it waits on that a condition skips, can skip it on a push or a pull request.
        if not isinstance(job, dict) or job.get("if") is not None or job.get("needs") is not None:
            continue
        steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
        for at, s in enumerate(steps):
            if s.get("if") is not None or s.get("background") is not None:   # the summary reads it once finished
                continue
            have = [line.strip() for line in str(s.get("run") or "").splitlines() if line.strip()]
            if want and any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)):
                if any(all(later.get(k) == SKIP_STEP[k] for k in ("if", "shell", "run")) for later in steps[at + 1:]):
                    return True
    return False


def readied(path: Path, run, spec: dict, final: str) -> str:
    """The report met, a README written, a workflow file only where the profile asks for one, and the
    adapter's whole gate green on the clean, committed HEAD."""

    o = spec["oneshot"]
    if why := oneshot_build.reported(spec, final):
        return why
    names = oneshot_build.changed(path, o.get("front_built_head") or o["tests_head"])
    if "README.md" not in names or not (path / "README.md").is_file():
        return "`README.md` 를 쓰지 않았다 — 설치, 실행, 테스트 명령을 적는다"
    # Before its gate is read, let alone run: the turn may have added `[release]` and nothing else. The gate is
    # read from that very commit's adapter, and the gate runs only on that commit (`judge`'s `expect`).
    try:
        with release.stoppable(run.halt):
            checked = release.git(path, "rev-parse", "HEAD").strip()
            adapter = kept_adapter(path, o, checked, tree=True)
            if moved := unapproved(path, o, checked, adapter):
                return ("R3 가 승인한 PC 앱 빌드 뒤에 앱 파일이 바뀌었다 — S8 은 README, `.wiki/`, CI 워크플로, 버전만 "
                        "담은 새 버전 파일만 바꾼다: " + ", ".join(moved[:5]))
    except (ValueError, release.Stopped) as exc:
        return str(exc)
    gate, backend = str((adapter.get("slots") or {}).get("gate_cmd", "")).strip(), o["gate"]
    if not gate:
        return "어댑터의 `gate_cmd` 가 비었다"
    if why := oneshot_build.published(gate, backend):
        return why
    # What the published HEAD holds, not what changed: a deleted workflow or a stray note is no CI.
    held = workflows(path)
    flows = sorted({*held, *(n for n in names if n.startswith(WORKFLOWS) and os.path.lexists(path / n))})
    public = o["profile"].get("visibility") == "public" and o["profile"].get("ci") == "github-actions"
    if flows and not public:
        return "이 저장소는 CI 워크플로를 두지 않는다 — " + ", ".join(flows[:5]) + " 를 지운다"
    if public and not ci_file(path, held.get(CI), gate):
        return (f"공개 저장소의 CI 워크플로(`{CI}`)가 없다 — 커밋된 보통 파일로, `on:` 트리거와 "
                f"게이트(`{gate}`)를 `run` 하는 job step, 같은 job 에서 그 뒤에 README 턴이 준 건너뛴 테스트 요약 step 을 그대로 둔다")
    if why := oneshot_build.pinned(path, o):
        return why
    # As at S7: the contract's command as the server stored it, then the adapter's gate, then each pinned case.
    ran = [backend, gate] if gate != backend else [gate]
    android = "android" in kinds_of(o)
    if android and (why := oneshot_device.cleared(path, o["device"])):   # the gate makes the APK frozen below
        return why
    env = oneshot_build.built_env(spec)   # read once: the freeze scan below looks for exactly these values
    verdict = oneshot_build.judged(path, run, spec, ran, env, expect=checked)
    if not verdict["ok"]:
        return f"최종 게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    # Each file S8 attaches, frozen before a later command can rebuild it: the APK the gate built, and the
    # installer the release table's build makes (`release_checked`). An earlier gate's copies go first.
    frozen, head, hide = {}, verdict["head"], oneshot_needs.hidden(spec, env)
    unstaged(spec)
    if android:
        if why := oneshot_device.built(path, o["device"]):
            return why
        frozen["apk"] = kept(spec, head, hide, "apk")
    why = "raised"   # until the later checks answer: a raise keeps it, and the copies go with it
    try:
        why = oneshot_build.held(path, run, spec, verdict) or bool(
            {"android", "desktop"} & set(kinds_of(o))) and release_checked(path, spec, head, run.halt, frozen, hide)
    finally:
        for copy in frozen.values() if why else ():   # failed or raised: no copy is ever kept
            if copy.get("path"):
                shutil.rmtree(Path(copy["path"]).parent, ignore_errors=True)
    if why:
        return why
    report = next(b["value"] for b in specs.blocks(final)[1] if b["name"] == "done-report")
    tail = (verdict.get("tail") or "").strip()
    # The pull request's body reads these as every spec's: the worker's report and the server's gate.
    specs.update(spec["repo"], spec["id"], report=report, gate={"cmd": "\n".join(ran), "head": verdict["head"],
                                                                "tail": tail, "selection": "full"})
    oneshot_spec.saved(spec["repo"], oneshot={"final_head": verdict["head"], "attempts": 0,
                                              **{f"{key}_frozen": copy for key, copy in frozen.items()}})
    specs.note(run, f"최종 게이트 통과 · {verdict['head'][:12]}")
    return ""


def version_file(path: Path, head: str, name: str) -> bool:
    """`name` at `head` holds the version alone: `{"version": ...}`, or TOML's `version` or `[package] version`."""

    try:
        text = release.git(path, "show", f"{head}:{name}")
        data = json.loads(text) if name.endswith(".json") else tomllib.loads(text)
    except ValueError:   # not in that commit, or a JSON or TOML error
        return False
    return isinstance(data, dict) and (set(data) == {"version"} or set(data) == {"package"}
                                       and isinstance(data["package"], dict) and set(data["package"]) == {"version"})


def scope(o: dict) -> str:
    """What the README turn may change, as `unapproved` checks it, told before it starts."""

    paths = (" Each `build` names only relative paths from the repository root that stay inside it: it runs in a "
             "fresh checkout.\n")
    if "android" in kinds_of(o):
        return ("\nThe PC app's installer is built from the commit R3 approved, so nothing this turn changes "
                "reaches it but its `[release.desktop]` table." + paths)
    return ("\nR3 approved the PC app's build, so this turn changes nothing else of the app: only the README, "
            "`.wiki/`, the CI workflow and a version file it adds holding the version alone. Any other change is "
            "refused." + paths)


def unapproved(path: Path, o: dict, head: str, adapter: dict) -> list[str]:
    """What changed since the PC app's build R3 approved that S8 may not change, for a PC app with no Android app
    (alone, or with a web app): anything but the README, `.wiki/`, the CI workflows and a version file the turn
    added holding the version alone (`version_source`), so the pull request's app is the installer's. With an
    Android app among the kinds, S8
    wires its version as an Android app alone does (`RELEASE_PROMPT`), and the installer is built from the
    approved commit regardless (`release_checked`). Empty without that build."""

    if not (approved := (o.get("desktop_build") or {}).get("head")) or "desktop" not in kinds_of(o) \
            or "android" in kinds_of(o):
        return []
    source = str((adapter.get("release") or {}).get("version_source") or "")
    added = set(release.git(path, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames",
                            "--diff-filter=A", "-z", approved, head).split("\0"))
    return [n for n in oneshot_build.changed(path, approved)
            if not (n == "README.md" or n.startswith((".wiki/", WORKFLOWS))
                    or n == source and n in added and version_file(path, head, n))]


# An absolute path: a drive, a share or a path rooted at the current drive, a home, a POSIX root with a folder in it.
# A switch such as `/p:x` is not one.
FAR = re.compile(r"(?<![\w$%./\\])(?:[A-Za-z]:[\\/]|\\|~[\\/]|/[^\s/:\"']+/)")
def outside(command: str) -> str:
    """A path in a release `build`, S8's own words, that leads out of its checkout, or empty: an absolute one, or
    one whose `..` steps climb above the repository root it runs from (`a/../b` stays inside). A command that names
    the live worktree would build bytes R3 never saw. A variable (`$env:X`) is not a path. What the approved
    commit's own files name, its Tauri config among them, is the app R3 built and approved (an accepted limit)."""

    if m := FAR.search(command):
        return command[max(m.start() - 10, 0):m.end() + 20].strip()
    parts = re.split(r"[\s\"'=,;]+", command)
    return next((p for p in parts if p and posixpath.normpath(p.replace("\\", "/")).split("/")[0] == ".."), "")


def tauri(o: dict) -> bool:
    """The PC app is a Tauri app: a choice the owner confirmed for one of its stack's parts (`ui`, `build` and
    the rest; `desktop.*` among several kinds) names Tauri. Another kind's parts are their own app's."""

    several = len(kinds_of(o)) > 1
    return any("tauri" in str(s.get("choice", "")).lower() for s in o.get("stack") or []
               if not several or str(s.get("part", "")).startswith("desktop."))


def release_checked(path: Path, spec: dict, head: str, halt: threading.Event, frozen: dict | None = None,
                    hide: dict | None = None) -> str:
    """The `[release]` table the README turn committed, read as a press reads it at `head`, and each kind it
    declares built once from its own clean checkout with the version a press would publish after the seed, so
    the build is shown to take the version it is given, then checked as a release checks what a build made
    (`release.built_wrong`, `release_desktop.built_wrong`). Android's is `head`'s; the PC app's is the commit R3
    approved (`desktop_build.head`), so nothing S8 committed after it reaches the installer but the table's own
    command. Unsigned: no key exists before the first press, and none is made. The installer so built and checked
    is the one S8 attaches: frozen into `frozen["installer"]` under `hide` before the checkout goes; a PC app with
    no `[release.desktop]` gets a record saying why it has none. Every command ends within a second of a stop,
    the checkouts' own included."""

    attempt, kind, made = f"oneshot-{time.time_ns()}", "android", []
    try:
        with release.stoppable(halt):
            try:
                cfg, later, kit = _declared(path, spec, head)
            except ValueError as exc:
                return f"`[release]` 표 — {exc}"
            if "desktop" in kinds_of(spec["oneshot"]) and "desktop" not in cfg:
                if tauri(spec["oneshot"]):
                    return ("`[release.desktop]` 가 없다 — 확정한 PC 앱 스택이 Tauri 다: README 턴이 그 표를 "
                            "`[release]` 에 쓴다")
                if frozen is not None:
                    frozen["installer"] = {"head": head, "error": NO_INSTALLER}
            approved = (spec["oneshot"].get("desktop_build") or {}).get("head") or head

            def pristine(src: Path, at: str) -> None:   # the bytes checked are of the commit as it is
                if release.git(src, "rev-parse", "HEAD").strip() != at or release.git(
                        src, "status", "--porcelain", "--untracked-files=no").strip():
                    raise release.BuildFailed("빌드가 체크아웃의 커밋된 파일을 바꿨다 — 정식 빌드는 소스를 고치지 않는다")

            # Built without the owner's secrets: no variable a secret need names, and none holding a value the
            # gate's masks carry. What ships can then hold a secret only from the source, which `publishable` reads.
            drop = frozenset({n["env"].upper() for _, n in oneshot_needs.secret_needs(spec)} | {
                k.upper() for k, v in os.environ.items() if any(s and s in v for s in hide or {})})
            for kind in [k for k in ("android", "desktop") if k in cfg]:
                at = approved if kind == "desktop" else head
                made.append(f"{attempt}-{kind}")
                src = release.checkout(path, at, made[-1])
                # A PC app's commands start only its confirmed tools or the checkout's own files, as its gate's do:
                # plain commands joined by `&&` (`chained`), then each program resolved (`run_as`).
                if oneshot_build.tools(spec["oneshot"]) is not None and not oneshot_build.chained(cfg[kind]["build"]):
                    raise ValueError(f"`build` 는 {oneshot_build.CHAIN}")
                if far := outside(cfg[kind]["build"]):   # the checkout is all it reads: no worktree file after R3
                    raise ValueError(f"`build` 가 체크아웃 밖의 경로를 가리킨다 — {far}: 저장소 안의 상대 경로만 쓴다")
                table = {**cfg[kind], "build": oneshot_build.run_as(cfg[kind]["build"], spec["oneshot"], src)}
                if kind == "android":
                    apk = release.build(src, table, later, kit, halt, drop=drop)
                    pristine(src, at)
                    seen = release.badging(release.run([kit["aapt2"], "dump", "badging", apk], release.environment(kit)))
                    wrong = release.built_wrong(seen, later, cfg[kind])
                else:
                    installer = release.build(src, table, later, kit, halt, read=release_desktop.readable, drop=drop)
                    pristine(src, at)
                    try:   # the installer and the app EXE the build made: one it does not hold is the build's
                        seen = release_desktop.facts(installer, release.output(src, cfg[kind], later, "exe"))
                    except ValueError as exc:
                        raise release.BuildFailed(f"빌드가 만든 설치 파일이나 앱 EXE 를 읽지 못했다 — {exc}") from exc
                    wrong = release_desktop.built_wrong(seen, later, cfg[kind])
                if wrong:
                    return f"`[release.{kind}]` 의 정식 빌드가 배포 검사에 걸린다 — {'; '.join(wrong)}"
                if kind == "desktop" and frozen is not None:
                    copy = kept(spec, head, hide or {}, "installer", installer)
                    if copy.get("sha256") not in (None, seen["sha256"]):   # the copy must be the bytes checked
                        shutil.rmtree(staging(spec, "installer"), ignore_errors=True)
                        copy = {"head": head, "error": "검사한 설치 파일과 복사한 설치 파일이 다르다"}
                    frozen["installer"] = {**copy, "version": later, "built": at}
                try:   # this kind's checkout goes before the next one is made, forgotten only once gone
                    release.discard(path, made[-1])
                    made.pop()
                except OSError:   # a file Windows holds for a moment: tried again below
                    pass
    except (ValueError, release.Stopped) as exc:   # `BuildFailed` among them
        return f"`[release.{kind}]` 의 정식 빌드 — {exc}"
    finally:
        with release.stoppable(None):   # the cleanup runs even after the stop
            for name in made:
                try:
                    if release.place(path, name).exists():
                        release.discard(path, name)
                except OSError as exc:   # recorded, never the check's answer: a leftover folder holds no result
                    errorlog.record("oneshot-release-checkout", exc, path=str(release.place(path, name)))
    return ""


def kept_adapter(path: Path, o: dict, head: str, tree: bool = False) -> dict:
    """The adapter at `head`, parsed, refused (`ValueError`) unless it, and the worktree's with `tree`, is the
    approved frontend's with at most `[release]` added: the README turn may write that table alone."""

    def rest(text: str) -> dict:
        return {k: v for k, v in tomllib.loads(text).items() if k != "release"}

    try:
        was = rest(release.git(path, "show", f"{o.get('front_built_head') or o['tests_head']}:{ADAPTER}"))
        at = tomllib.loads(release.git(path, "show", f"{head}:{ADAPTER}"))
        now = [{k: v for k, v in at.items() if k != "release"},
               *([rest((path / ADAPTER).read_bytes().decode("utf-8"))] if tree else [])]
    except (ValueError, OSError) as exc:   # a TOML error is a `ValueError`
        raise ValueError(f"`{ADAPTER}` 를 읽지 못했다 — {exc}") from exc
    if any(n != was for n in now):
        raise ValueError(f"`{ADAPTER}` 의 `[release]` 밖이 바뀌었다 — 그 표만 더하고 나머지는 그대로 둔다")
    return at


def _declared(path: Path, spec: dict, head: str) -> tuple[dict, str, dict]:
    """The release table at `head` (`release.settings`), the version its builds are made with, and the tools; a
    `ValueError` says why not. An Android app needs its table; a PC app's is optional, and with no `[release]`
    at all a PC app alone gives `({}, "", {})`. A kind the run does not have has no table. The adapter around
    it is the approved frontend's (`kept_adapter`)."""

    o, kinds = spec["oneshot"], kinds_of(spec["oneshot"])
    if "release" not in kept_adapter(path, o, head) and "android" not in kinds:
        return {}, "", {}
    cfg = release.settings(path, head)
    if "android" in kinds and "android" not in cfg:
        raise ValueError(f"`{ADAPTER}` 에 `[release.android]` 가 없다 — 정식 빌드 명령, APK 경로, 앱 ID, ABI 를 적는다")
    if extra := [k for k in release.KINDS if k in cfg and k not in kinds]:
        raise ValueError(f"`[release.{extra[0]}]` 는 이 앱의 종류가 아니다 — 앱이 가진 종류의 표만 쓴다")
    first = release.seed(path, head, cfg["version_source"])
    if "android" in cfg and cfg["android"]["application_id"] != (want := o["device"]["package"]):
        raise ValueError(f"`[release.android]` 의 `application_id` {cfg['android']['application_id']!r} 가 앱의 {want!r} 와 다르다")
    release.version([], [], None, first, cfg)   # a seed a declared kind cannot carry, refused as the card would
    # A version a press could publish next: a build that keeps the source's version instead fails on it.
    for bump in release.BUMPS:   # a patch past what Android carries moves on to the minor, as a person would
        try:
            return cfg, release.version([{"tagName": f"v{first}"}], [], bump, first, cfg), release.tools(cfg)
        except ValueError:
            if bump == release.BUMPS[-1]:
                raise


def decisions(o: dict) -> list[str]:
    """What the owner decided at the three reviews, for the pull request's `변경 이유`."""

    # Read off each submission's record: an applied edit's comment is gone from the sheet's decisions.
    out = [f"{gate.upper()} {item} (round {entry['round']}): {comment}"
           for gate in ("r1", "r2", "r3") for entry in (o.get("reading") or {}).get(gate) or []
           for item, comment in (entry.get("edits") or {}).items()]
    if palette := (o.get("r2") or {}).get("palette"):
        out.append(f"R2 palette: {palette}")
    return out


def kept(spec: dict, head: str, hide: dict, key: str, source: Path | None = None) -> dict:
    """`froze`, or why it could not: the attachment is optional, so the pull request opens and its body says why."""

    try:
        return froze(spec, head, hide, key, source)
    except OSError as exc:
        return {"head": head, "error": f"최종 게이트가 만든 {ASSETS[key][2]} 복사하지 못했다 — {exc}"}


def staging(spec: dict, key: str) -> Path:
    """Where the frozen copy of `key`'s file lives (`froze`)."""

    return oneshot_spec.store(spec["repo"]).folder / ("release" if key == "apk" else f"release-{key}")


def unstaged(spec: dict) -> None:
    """Every frozen copy removed: a new final gate freezes its own, and a run whose files will not go up keeps none."""

    for key in ASSETS:
        shutil.rmtree(staging(spec, key), ignore_errors=True)


def froze(spec: dict, head: str, hide: dict, key: str = "apk", source: Path | None = None) -> dict:
    """The file S8 attaches (`ASSETS`), copied out while the check holds it, with its digest: the APK the final gate
    built in the worktree, a build output Git does not track, or the installer at `source`, which the release
    table's build made in its checkout (`release_checked`). Nothing read later is then other than what was
    checked. An earlier copy of the same file goes first."""

    base = staging(spec, key)
    shutil.rmtree(base, ignore_errors=True)
    source = Path(spec["worktree"]) / spec["oneshot"]["device"]["apk"] if key == "apk" else source
    name = f"{spec['repo']}-{head[:12]}" + ("-debug.apk" if key == "apk" else release_desktop.SUFFIX)
    into = base / f"r{time.time_ns()}"
    try:
        into.mkdir(parents=True)
        shutil.copyfile(source, into / name)
        # Read under the values its build ran with (`hide`, from the gate's own environment): an upload later,
        # after a value changed or was taken back, still needs this scan, bound to these bytes by their digest.
        scan = oneshot_needs.asset(into / name, spec, hide, packed=key == "installer")
        digest = android_owned.sha256(into / name)
        if scan:   # never uploaded, so never kept: the record keeps why
            shutil.rmtree(into, ignore_errors=True)
            return {"head": head, "name": name, "sha256": digest, "scan": scan}
        return {"head": head, "name": name, "path": str(into / name), "sha256": digest, "scan": scan}
    except BaseException:
        shutil.rmtree(into, ignore_errors=True)   # never a partial copy left behind, whatever stopped it
        raise


def released(spec: dict, keys: list[str]) -> dict:
    """The files the final gate froze (`keys` of `ASSETS`), attached to one draft prerelease of the repository for
    its head, which the pull request's body links: each key's URL, asset name and digest and the head, or why it
    could not. The owner decided on 2026-11-16 that a Release holds them: it works for a private repository and
    keeps."""

    path, head = Path(spec["worktree"]), spec["oneshot"]["final_head"]
    tag, made = f"oneshot-{head[:12]}", []   # `made`: the prerelease once had, or why it could not be

    def prerelease() -> tuple[str, dict]:
        if not made:
            try:
                made.append(_prerelease(spec, github_release.slug(release.remote(path)), head, tag))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                made.append(exc)
        if isinstance(made[0], Exception):   # asked once: the next file says the same reason
            raise made[0]
        return made[0]

    return {key: _released(spec, key, prerelease, tag) for key in keys}


def _prerelease(spec: dict, repo: str, head: str, tag: str) -> tuple[str, dict]:
    """This head's draft prerelease, made unless already there (an earlier upload, or a create whose reply was
    lost): a second draft of the same tag would leave the first behind. A transient failure is tried again, a
    bounded number of times, as GitHub's own CLI did."""

    def found() -> dict | None:   # only a draft prerelease is S8's: anything else of that tag is left untouched
        held = next((r for r in github_release.releases(repo) if r.get("tag_name") == tag), None)
        if held and not (held.get("draft") and held.get("prerelease")):
            raise ValueError(f"태그 `{tag}` 의 릴리스가 초안 프리릴리스가 아니다 — 거기에 올리지 않는다")
        return held

    for n in range(TRIES):
        if held := found():
            return repo, held
        try:
            return repo, github_release.create(repo, tag, None, f"{spec['repo']} 원샷 빌드 · {head[:12]}",
                                               f"원샷 실행의 첫 PR 이 링크하는 빌드 산출물이다. 빌드 커밋 `{head}`.",
                                               draft=True, prerelease=True)
        except github_release.Failed as exc:   # a transient one is looked for again at the loop's top
            if not exc.transient or n == TRIES - 1:
                if held := found():   # it was made; only the answer was lost
                    return repo, held
                raise
    raise AssertionError("unreachable")


def _released(spec: dict, key: str, prerelease, tag: str) -> dict:
    """One frozen file on the head's prerelease (`prerelease()` has it), its copy removed whatever happens."""

    o, (_, bare, scanned, subject) = spec["oneshot"], ASSETS[key]
    head, frozen, old = o["final_head"], o.get(f"{key}_frozen") or {}, o.get(key) or {}
    if frozen.get("head") != head or frozen.get("error"):
        return {"head": head, "error": frozen.get("error") or f"최종 게이트가 고정한 {subject} 없다"}
    # Flagged at the freeze, whose copy went then: refused before anything, an earlier upload of it included.
    if why := ("scan" not in frozen and f"최종 게이트가 고정한 {scanned} 검사한 기록이 없다") or frozen["scan"]:
        return {"head": head, "error": why}
    # A resumed S8 asks for the text again: the same file is not uploaded twice, a rebuilt one replaces it.
    if old.get("url") and (old.get("head"), old.get("sha256")) == (head, frozen["sha256"]):
        shutil.rmtree(Path(frozen["path"]).parent, ignore_errors=True)
        return old
    file = Path(frozen["path"])
    try:
        if not file.is_file() or android_owned.sha256(file) != frozen["sha256"]:
            raise ValueError(f"최종 게이트가 고정한 {bare} 사본이 없거나 바뀌었다")
        # The exact bytes that go up: clean under the values the build ran with (`froze`) and under today's.
        if why := oneshot_needs.asset(file, spec, packed=key == "installer"):
            raise ValueError(why)
        repo, held = prerelease()
        failed = None
        for n in range(TRIES + 1):   # the read after the last try decides: a last upload that landed counts
            # Read fresh each time: the owner may publish the draft meanwhile, and a published release is not S8's.
            if not ((now := github_release.release(repo, held["id"])).get("draft") and now.get("prerelease")):
                raise ValueError(f"태그 `{tag}` 의 릴리스가 더는 초안 프리릴리스가 아니다 — 거기에 올리지 않는다")
            mine = next((a for a in now.get("assets") or [] if a["name"] == frozen["name"]), None)
            # Already there with these bytes: the try before landed, or an earlier S8's whose record was never saved.
            if mine and mine.get("state") == "uploaded" and github_release.digest(mine) == frozen["sha256"]:
                break
            if n == TRIES:
                raise failed
            if mine:   # S8's own policy: the asset of that name is replaced
                github_release.delete_asset(repo, mine["id"])
            try:
                github_release.upload(repo, held["id"], file, frozen["name"])
                break
            except github_release.Failed as exc:
                if not exc.transient:
                    raise
                failed = exc
        return {"head": head, "name": frozen["name"], "sha256": frozen["sha256"], "url": held["html_url"], "tag": tag,
                **{f: frozen[f] for f in ("version", "built") if frozen.get(f)}}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"head": head, "error": str(exc) or type(exc).__name__}
    finally:
        shutil.rmtree(file.parent, ignore_errors=True)


def asked(repo: str) -> None:
    """The gate passed: an Android app's APK and a PC app's installer go up for the pull request to link, then
    one turn asks the worker for the pull request's text, as every spec's does."""

    spec = oneshot_spec.spec_of(repo)
    if why := oneshot_needs.publishable(Path(spec["worktree"]), spec):   # before anything goes out
        unstaged(spec)   # a resume runs the final gate again, which freezes its own
        return oneshot_spec.store(repo).stop(STAGE, why) and None   # stopped: no next turn
    if keys := [key for key, (kind, *_) in ASSETS.items() if kind in kinds_of(spec["oneshot"])]:   # alone or among several
        spec = oneshot_spec.saved(repo, oneshot={**released(spec, keys), "attached": spec["oneshot"]["final_head"]})
    if oneshot_spec.held(spec["oneshot"]):   # a PC app's pull request is the next step's: the stop says what went up
        return oneshot_spec.store(repo).stop("kind", oneshot_spec.kind_stop(STAGE, spec["oneshot"])["detail"]) and None
    path, head = Path(spec["worktree"]), oneshot_spec.spec_of(repo)["oneshot"]["final_head"]
    spec = specs.update(repo, spec["id"], writing={"head": head, "asked": 1})
    said = decisions(spec["oneshot"])
    text = pr_text.writing(spec, specs.channels.repo_for(repo), path, threading.Event())
    if said:
        text += ("\n\nThe owner's decisions at the three reviews, which belong in `why`:\n"
                 + "\n".join(f"- {d}" for d in said))
    oneshot_build.send(repo, STAGE, text)


def handed(path: Path, run, spec: dict, final: str):
    """`oneshot_build.checked`'s hand-over while a pull request's text is asked: `specs.written` checks it
    and opens the pull request; the run then keeps its summary and the spec its `PR #n`."""

    repo = spec["repo"]
    run.checked = True
    # The text asked again is the run's own turn: charged, and stopping the stage if it fails.
    ask = lambda text: oneshot_build.send(repo, STAGE, text) or True   # noqa: E731 — sent stops itself on a refusal
    if why := oneshot_publish.unready(spec, path):   # before `specs.opened` pushes: the draft, or nothing
        oneshot_spec.store(repo).stop(STAGE, why)
        return None
    try:
        then = specs.written(specs.channels.repo_for(repo), path, run, spec, final, ask)
    except Exception as exc:   # a push that timed out: stopped with its reason, resumed from the README turn
        oneshot_spec.store(repo).stop(STAGE, masked(f"PR 을 만들지 못했다 — {type(exc).__name__}: {exc}",
                                                    getattr(run.chat, "hide", None)))
        raise
    fresh = specs.load(repo, spec["id"])
    # Published by this hand-over only when no fault and no text asked again stands: an earlier publication's
    # record, left by a push that failed since or a text turn that moved HEAD, never finishes the run.
    if fresh.get("pr") and not fresh.get("fault") and not fresh.get("writing"):
        if spec["oneshot"].get("pr1"):
            # `specs.opened` published only to the draft the backend opened (`oneshot_publish.published`), never a
            # replacement: the final text is in, so it leaves draft. A draft left stops, and `[재개]` asks again.
            try:
                ready = specs.sh(["gh", "pr", "ready", str(fresh["pr"]["number"])], path, 60)
                why = specs.said(ready) if ready.returncode else ""
            except (OSError, subprocess.TimeoutExpired) as exc:
                why = f"{type(exc).__name__}: {exc}"
            if why:
                oneshot_spec.store(repo).stop(STAGE, f"PR #{fresh['pr']['number']} 의 초안을 풀지 못했다 — {why}")
                return None
        oneshot_spec.saved(repo, oneshot={"stage": "done", "working": None, "pr": fresh["pr"]})
    elif not fresh.get("writing"):   # neither open nor asked again: the reason is the spec's fault
        oneshot_spec.store(repo).stop(STAGE, fresh.get("fault") or "PR 을 만들지 못했다")
    elif then:   # `working` stays until the turn goes: a restart in between is recovered as any running stage
        return oneshot_build.unless_stopped(run, repo, then)
    return then


oneshot_build.STAGES[STAGE] = (readied, STAGE, restart, asked)
# An Android or PC app's README turn writes `[release]`; the pull request's text turn (`writing`) writes nothing there.
oneshot_build.GRANTS[STAGE] = lambda spec: bool({"android", "desktop"} & set(kinds_of(spec["oneshot"]))) \
    and not spec.get("writing")
oneshot_build.HANDOVERS[STAGE] = lambda spec: bool(spec.get("writing")), handed
oneshot_spec.TURNS.add(STAGE)


def summary(spec: dict) -> dict:
    """The finished run's one summary: the sentence, each gate's rounds and reading, the spend, the
    critique rounds and the pull request."""

    o = spec["oneshot"]
    reading = o.get("reading") or {}
    return {"sentence": o.get("sentence", ""), "pr": o.get("pr"),
            "gates": [{"gate": g, "rounds": (o.get(g) or {}).get("round", 0), "reading": reading.get(g) or [],
                       "edited": sorted({i for e in reading.get(g) or [] for i in e.get("edited") or []})}
                      for g in ("r1", "r2", "r3")],
            "spend": o.get("spend") or {}, "critiques": len(o.get("critiques") or []),
            "looks": len(o.get("looks") or []), "apk": o.get("apk"), "installer": o.get("installer"), "calls": oneshot_calls.summary(spec),
            "ci": (o.get("profile") or {}).get("visibility") == "public" and (o.get("profile") or {}).get("ci") == "github-actions"}


def extra(spec: dict) -> dict:
    return {"summary": summary(spec)} if spec["oneshot"].get("stage") == "done" else {}


oneshot_spec.VIEWS.append(extra)
