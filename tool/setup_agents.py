"""Install a checkout's rules through `apply.py`.

Downloads nothing and changes no host's trust setting unless the person asks
with `--trust-codex`, and then only for this wiki's own dispatcher. Everything printed is read by
whoever is running the install, so those strings are Korean.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

WIKI = Path(__file__).resolve().parents[1]
SETTINGS = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}



def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=45,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if result.returncode:
        raise ValueError(f"명령 실패 ({result.returncode}): {command[0]}\n{result.stdout}{result.stderr}")
    return result.stdout


def hook_shell(agent):
    if os.name != "nt":
        return ["/bin/sh", "-c"]
    if agent == "codex":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            return [shell, "-NoProfile", "-NonInteractive", "-Command"]
        raise ValueError("Codex hook 실행에 필요한 PowerShell을 찾지 못했습니다.")
    # This wiki version's Claude commands are in Git Bash form, which is not
    # WSL's `bash.exe` — the two are told apart rather than assumed.
    git = shutil.which("git")
    bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if not bash and git:
        # Git for Windows ships git.exe three times -- `cmd/`, `bin/` and
        # `mingw64/bin/` -- and only the install root holds `bin/bash.exe`.
        # One level up reaches it from `cmd/` and lands on `mingw64` from the
        # third, which is the copy PATH points at on a machine that installed
        # the MinGW tools. So walk up instead of assuming a depth. Three levels
        # is the deepest of the three layouts and keeps the search inside the
        # Git install, where a match cannot be WSL's bash.exe.
        for parent in Path(git).resolve().parents[:3]:
            if (parent / "bin/bash.exe").is_file():
                bash = str(parent / "bin/bash.exe")
                break
    if bash and Path(bash).is_file():
        return [bash, "--noprofile", "--norc", "-c"]
    raise ValueError("이 위키 버전의 Claude hooks는 Git for Windows의 Git Bash가 필요합니다. "
                     "설치 후 CLAUDE_CODE_GIT_BASH_PATH를 실제 bash.exe 경로로 지정하세요.")


def install(project, choice, check, allow_dirty=False):
    wiki = WIKI
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 이상이 필요합니다. 새 Python으로 이 명령을 다시 실행하세요.")
    import tomllib

    # The hooks' package check is deliberately not here. `apply.py` probes the
    # interpreter the hooks will actually run under, right before it writes
    # the wiring. Checking again here would examine this process's
    # interpreter, which may not be that one at all.
    if not (wiki / "tool/apply.py").is_file():
        raise ValueError(f"위키 경로에 tool/apply.py가 없습니다: {wiki}. 완전한 위키 checkout을 사용하세요.")
    for path in (project, wiki, Path(sys.executable)):
        if any(char in str(path) for char in ('"', '$', '`', '\n', '\r')):
            raise ValueError(f"고정 위키의 셸 인용이 지원하지 않는 문자가 경로에 있습니다: {path}. "
                             "따옴표·달러·백틱·줄바꿈 없는 경로로 옮기세요. 공백·한글은 지원합니다.")
    actual = run(["git", "rev-parse", "HEAD"], wiki).strip()
    if project == wiki:
        # The wiki targeting itself. Both the version pin and the dirty check
        # become self-referential: the pin goes stale on every commit, and
        # working on the tools means always being dirty. The hooks being
        # installed point at this working tree anyway, so the question "are
        # you on a different version" has nothing to compare.
        revision = actual
        print("자기 설치: 이 checkout의 현재 상태를 그대로 겁니다. 위키 버전 고정 검사는 하지 않습니다.")
    else:
        revision = (project / ".wiki/wiki-revision").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or actual != revision:
            raise ValueError(f"위키 버전 불일치: 기대 {revision}, 현재 {actual}. "
                             "기존 작업을 보존한 별도 checkout을 준비하세요. 자동 checkout은 하지 않습니다.")
        runtime = ("tool", "operator", "craft", "skills")
        dirty = run(["git", "diff", "--name-only", "HEAD", "--", *runtime], wiki)
        dirty += run(["git", "ls-files", "--others", "--exclude-standard", "--", *runtime], wiki)
        if dirty.strip() and not allow_dirty:
            raise ValueError("고정 버전과 다른 위키 실행 코드·규칙이 있습니다. 깨끗한 별도 checkout을 사용하세요:\n" + dirty)
        if allow_dirty:
            print("개발 검증: --allow-dirty-wiki 사용. 고정 버전의 배포 검증으로 세지 않습니다.")

    # A parent session's test `WIKI_ROOT` may point at another hub. What gets
    # installed is still this tool, from this checkout.
    os.environ["WIKI_ROOT"] = str(wiki)
    from apply import installed_agents, unfilled
    source = project / ".wiki/adapter.toml"
    canonical = tomllib.loads(source.read_text(encoding="utf-8"))
    declared = canonical.get("agents", list(SETTINGS))
    if not isinstance(declared, list) or not declared or any(a not in SETTINGS for a in declared):
        raise ValueError(f"{source}의 agents는 claude/codex 목록이어야 합니다.")
    missing = unfilled(None, project)
    if missing:
        raise ValueError(f"{source}의 안 채워진 슬롯: {', '.join(sorted(missing))}")

    agents = tuple(SETTINGS) if choice == "both" else (choice,)
    if any(agent not in declared for agent in agents):
        raise ValueError(f"선택한 호스트가 {source}의 agents에 없습니다: {choice}")
    configs = {}
    for agent, relative in SETTINGS.items():
        path = project / relative
        configs[agent] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for agent in agents:
        binary = shutil.which(agent)
        if not binary:
            raise ValueError(f"{agent} CLI를 PATH에서 찾지 못했습니다. 해당 호스트의 공식 설치 안내 후 재실행하세요.")
        print(run([binary, "--version"], project).strip())
        shell = hook_shell(agent)
        run([*shell, "exit 0"], project)
        if configs[agent].get("disableAllHooks"):
            raise ValueError(f"{SETTINGS[agent]}에서 disableAllHooks가 켜져 있습니다. 직접 검토 후 해제하세요.")
        if agent == "codex":
            features = run([binary, "features", "list"], project)
            hooks = re.search(r"^hooks\s+(.+?)\s+(true|false)\s*$", features, re.M)
            if not hooks or hooks[1].strip() in ("removed", "deprecated"):
                raise ValueError("이 Codex CLI는 필요한 hooks 기능을 제공하지 않습니다. 공식 지원 버전으로 갱신하세요.")
            config = tomllib.loads((project / ".codex/config.toml").read_text(encoding="utf-8"))
            if config.get("features", {}).get("hooks") is not True:
                raise ValueError("프로젝트 .codex/config.toml의 [features] hooks = true를 검토하세요. 자동 덮어쓰기는 하지 않습니다.")
            for line in features.splitlines():
                if line.startswith(("hooks ", "default_mode_request_user_input ")):
                    print(line)
            print("Codex 프로젝트 신뢰와 /hooks 검토가 필요합니다. 질문 UI는 현재 호스트 도구 명세를 따르며, "
                  "미지원 시 Plan mode 또는 텍스트 선택지를 사용하세요.")

    # An existing install for a host not chosen this time is preserved.
    # Re-running with a different choice does not remove it.
    previous = installed_agents(project) or []
    expected = [agent for agent in SETTINGS if agent in agents or agent in previous or any(
        "tool/inject.py" in hook.get("command", "")
        for group in configs[agent].get("hooks", {}).get("UserPromptSubmit", [])
        for hook in group.get("hooks", [])
    )]
    manifest = project / ".wiki/installed-agents.json"
    if check and previous != expected:
        raise ValueError("설치 호스트 목록이 미설치 또는 선택한 호스트와 다릅니다. --check 없이 설치하세요.")

    files = [manifest, *(project / SETTINGS[agent] for agent in agents)]
    before = {path: path.read_bytes() if path.exists() else None for path in files}
    try:
        # Writing starts only after the shared tool has read every settings
        # file. A half-written install across two hosts is worse than none.
        commands = [[sys.executable, "-X", "utf8", str(wiki / "tool/apply.py"),
                     "--project", str(project), "--agent", agent]
                    for agent in agents]
        if not check:
            for command in commands:
                run(command, project)
            for agent, command in zip(agents, commands):
                run([*command, "--write"], project)
            manifest.write_text(json.dumps(expected) + "\n", encoding="utf-8", newline="\n")
        for agent, command in zip(agents, commands):
            run([*command, "--check"], project)
            print(f"{agent}: apply --check 통과")
    except Exception:
        if not check:
            for path, data in before.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
        raise
    print(f"설치 배선 검사 완료: 위키 기준 {revision}\n"
          "이 명령은 실제 자동 이벤트·선택형 질문 UI를 검증하지 않습니다. 새 세션에서 별도로 확인하세요.")


def codex_hooks(home, trust):
    """This wiki's hooks as one Codex home sees them, trusted first if asked.

    Codex runs a hook only while `hooks.state` holds the hash of exactly that
    entry, and any change to the entry — a timeout, a path — drops it back to
    "modified". Trust is still the person's call: only `--trust-codex` writes
    it, only for commands that run this checkout's `hook.py`, and through
    Codex's own config writer rather than by editing its file.
    """
    from apply import installed
    from agent.chat_local import CodexServer

    # Exactly what this install wrote, event and command both. Anything that
    # merely names or resembles the dispatcher is somebody else's code, and
    # trusting it would vouch for that code.
    mine = {(event[0].lower() + event[1:], command)
            for event, command in installed("codex", sys.executable)}
    with CodexServer(env={"CODEX_HOME": str(home)}, cwd=WIKI) as server:
        listed = lambda: [h for d in server.request("hooks/list", {"cwds": [str(WIKI)]})["data"]  # noqa: E731
                          for h in d["hooks"] if (h["eventName"], h["command"]) in mine]
        hooks = listed()
        pending = {h["key"]: {"trusted_hash": h["currentHash"]} for h in hooks if h["trustStatus"] != "trusted"}
        if trust and pending:
            server.request("config/batchWrite", {"edits": [
                {"keyPath": "hooks.state", "value": pending, "mergeStrategy": "upsert"}]})
            hooks = listed()
    return hooks


def probe(project):
    """Run the SessionStart hook the way a host would, from `project`."""
    done = subprocess.run(
        [sys.executable, str(WIKI / "tool/hook.py"), "claude", "session_state.py"],
        input=json.dumps({"cwd": str(project)}), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    return "additionalContext" in done.stdout


def install_global(choice, check, projects, trust):
    """Attach the wiki once, at each host's user level.

    Every checkout on the machine — a worktree Orca opens after an update
    included — reads these files, and `hook.py` works out the project per
    call. The commands carry nothing that changes with the project, so the
    entries, and with them Codex's trust hashes, stay put.
    """
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 이상이 필요합니다. 새 Python으로 이 명령을 다시 실행하세요.")
    for path in (WIKI, Path(sys.executable)):
        if any(char in str(path) for char in ('"', '$', '`', '\n', '\r')):
            raise ValueError(f"셸 인용이 지원하지 않는 문자가 경로에 있습니다: {path}")
    os.environ["WIKI_ROOT"] = str(WIKI)
    from apply import configure, installed, keep_denies, read_json, restricted, unusable, unwire, user_files

    missing = unusable(sys.executable)
    if missing:
        raise ValueError(f"{sys.executable} 이 {', '.join(missing)} 를 못 읽습니다. "
                         "requirements-hooks.txt 를 설치한 Python으로 다시 실행하세요.")
    # Everything that can refuse, refuses before the first write. A mistyped
    # path would otherwise be created and handed twelve deny rules, and a
    # missing second CLI would leave the first host installed alone.
    from workspace.sessions import checkout

    for project in projects:
        top = checkout(project)[0]
        if not top or os.path.normcase(top) != os.path.normcase(project) or not (project / ".wiki/adapter.toml").is_file():
            raise ValueError(f"위키가 붙은 git checkout 의 루트가 아닙니다 (.wiki/adapter.toml 필요): {project}")
    agents = tuple(SETTINGS) if choice == "both" else (choice,)
    for agent in agents:
        binary = shutil.which(agent)
        if not binary:
            raise ValueError(f"{agent} CLI를 PATH에서 찾지 못했습니다.")
        print(run([binary, "--version"], WIKI).strip())
        run([*hook_shell(agent), "exit 0"], WIKI)
    # Every file is read and every change worked out before the first write:
    # a broken JSON in the last file must not leave the first one rewritten.
    plan = []
    refusals = []
    for agent in agents:
        for path in user_files(agent):
            settings = read_json(path)
            plan.append((path, settings, configure(settings, None, None, sys.executable, agent)))
            refusals += [f"{path}: {r}" for r in restricted(settings)]
        for project in projects:
            path = project / SETTINGS[agent]
            if not path.exists() and agent != "claude":
                continue
            settings = read_json(path)
            # The named checkout keeps its deny rules where the host enforces
            # them, so a failed hook does not let `git reset --hard` through.
            changes = unwire(settings) + (keep_denies(settings) if agent == "claude" else [])
            plan.append((path, settings, changes))
    # Claude honours `disableAllHooks` from any layer, and nothing here can
    # tell from the files alone that the host will skip what they wire.
    if "claude" in agents:
        for path in [*user_files("claude"), *(p / ".claude" / name for p in projects
                                              for name in ("settings.json", "settings.local.json"))]:
            if read_json(path).get("disableAllHooks"):
                refusals.append(f"{path}: disableAllHooks 가 켜져 있다")
    if refusals:
        raise ValueError("설치로 고칠 수 없는 설정이 있습니다. 직접 검토하세요:\n- " + "\n- ".join(refusals))

    broken = []
    for path, settings, changes in plan:
        if check:
            broken += [f"{path}: {change}" for change in changes]
        elif changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8", newline="\n")
            print(f"썼다: {path} ({len(changes)}건)")
        else:
            print(f"그대로: {path}")
    for agent in agents:
        if agent == "codex":
            for path in user_files("codex"):
                hooks = codex_hooks(path.parent, trust and not check)
                untrusted = [h["key"] for h in hooks if h["trustStatus"] != "trusted"]
                # What Codex actually loaded, against what the file holds. Zero
                # untrusted out of zero loaded is Codex not reading the file —
                # hooks switched off, or a home it does not start from.
                expected = installed("codex", sys.executable)
                wired = sum((event, h.get("command", "")) in expected
                            for event, gs in read_json(path).get("hooks", {}).items()
                            for g in gs for h in g.get("hooks", []))
                print(f"Codex {path.parent}: 위키 훅 {len(hooks)}/{wired}개 읽힘, 미신뢰 {len(untrusted)}개")
                if len(hooks) < wired:
                    broken.append(f"{path.parent}: Codex가 위키 훅 {wired}개 중 {len(hooks)}개만 읽는다. "
                                  "그 홈의 config.toml [features] hooks 를 확인하세요")
                if untrusted:
                    broken.append(f"{path.parent}: Codex 신뢰 대기 {len(untrusted)}개. "
                                  "`--trust-codex` 로 신뢰하거나 Codex /hooks 에서 검토하세요")
    # A named project has to come out injected. The current folder is only
    # looked at: it may be a checkout that never attached the wiki.
    for project in projects or [Path.cwd()]:
        injected = probe(project)
        print(f"SessionStart 시험 — {project}: {'주입됨' if injected else '주입 없음'}")
        if projects and not injected:
            broken.append(f"{project}: SessionStart 가 아무것도 주입하지 않았다 "
                          "(.wiki/adapter.toml 이 없거나 훅이 실패했다)")
    if broken:
        raise ValueError("전역 배선이 어긋났습니다:\n- " + "\n- ".join(broken))
    print("전역 배선 검사 완료. CLI를 업데이트한 뒤에는 `--global --check` 만 다시 돌리세요.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, action="append",
                        help="대상 checkout (기본: 현재 폴더). --global 에서는 옛 프로젝트 훅을 걷을 곳, 여러 번 줄 수 있다")
    parser.add_argument("--agent", choices=("claude", "codex", "both"), default="both")
    parser.add_argument("--check", action="store_true", help="설정 쓰기 없이 선택한 호스트의 배선만 검사")
    parser.add_argument("--global", dest="everywhere", action="store_true",
                        help="사용자 단위 설정에 한 번 건다. 모든 checkout과 작업트리가 읽는다")
    parser.add_argument("--trust-codex", action="store_true",
                        help="--global 과 함께. 이 위키의 hook.py 를 부르는 Codex 훅만 신뢰로 기록한다")
    parser.add_argument("--allow-dirty-wiki", action="store_true", help="미커밋 위키 개발 검증 전용. SHA 일치는 여전히 필수")
    args = parser.parse_args()
    projects = [p.expanduser().resolve() for p in args.project or []]
    try:
        if args.everywhere:
            install_global(args.agent, args.check, projects, args.trust_codex)
            return 0
        install((projects or [Path.cwd().resolve()])[0], args.agent, args.check, args.allow_dirty_wiki)
        return 0
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError,
            subprocess.TimeoutExpired) as error:
        print(f"설치 실패: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
