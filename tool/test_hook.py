"""The user-level install: one wiring for every checkout, worktrees included."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import apply  # noqa: E402
import hook  # noqa: E402


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def attached_repo(root: Path) -> tuple[Path, Path]:
    """A clone carrying `.wiki/adapter.toml` only in its own checkout, plus a worktree of it."""

    main = root / "main clone"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "0")
    (main / ".wiki").mkdir()
    (main / ".wiki/adapter.toml").write_text('agents=["claude"]\n', encoding="utf-8")
    tree = root / "작업트리"
    git(main, "worktree", "add", "-q", str(tree))
    return main, tree


def test_worktree_resolves_to_the_attached_clone():
    with tempfile.TemporaryDirectory() as raw:
        main, tree = attached_repo(Path(raw))
        project, top = hook.project_for(tree / ".")
        assert project.resolve() == main.resolve(), "작업트리는 adapter를 가진 원래 clone의 위키를 쓴다"
        assert top.resolve() == tree.resolve(), "브랜치는 세션이 있는 작업트리에서 읽는다"
        assert hook.project_for(Path(raw)) is None, "저장소가 아니면 붙지 않는다"


def test_the_injector_is_told_which_host_it_runs_on():
    """Without `--host` the injector knows no ceiling and deduplicates
    nothing — every page goes out in full on every turn, silently."""

    import io

    with tempfile.TemporaryDirectory() as raw:
        _main, tree = attached_repo(Path(raw))
        ran = {}
        was = hook.runpy.run_path, sys.stdin, sys.stdout, sys.argv
        hook.runpy.run_path = lambda path, run_name=None: ran.setdefault(Path(path).name, sys.argv[1:])
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"cwd": str(tree)}).encode("utf-8")))
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        try:
            hook.main(["codex", "inject.py"])
        finally:
            hook.runpy.run_path, sys.stdin, sys.stdout, sys.argv = was
    args = ran["inject.py"]
    assert args[args.index("--host") + 1] == "codex", args


def test_unattached_directory_passes_silently():
    with tempfile.TemporaryDirectory() as raw:
        done = subprocess.run(
            [sys.executable, str(HERE / "hook.py"), "claude", "inject.py"],
            input=json.dumps({"cwd": raw, "prompt": "안녕"}), capture_output=True,
            text=True, encoding="utf-8",
        )
        assert done.returncode == 0 and not done.stdout, "위키가 안 붙은 곳에서는 아무것도 내지 않는다"


def test_old_project_install_keeps_the_job():
    with tempfile.TemporaryDirectory() as raw:
        top = Path(raw)
        (top / ".claude").mkdir()
        settings = {}
        apply.configure(settings, top, None, "C:/py.exe", "claude")
        (top / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")
        assert hook.legacy(top, "claude", "inject.py"), "옛 설치가 있으면 두 번 돌지 않는다"
        assert not hook.legacy(top, "codex", "inject.py")


def test_user_level_wiring_round_trips():
    for agent in ("claude", "codex"):
        settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo 남의-훅"}]}]}}
        assert apply.configure(settings, None, None, "C:/py.exe", agent)
        assert not apply.configure(settings, None, None, "C:/py.exe", agent), "다시 걸어도 그대로다"
        commands = [h["command"] for gs in settings["hooks"].values() for g in gs for h in g["hooks"]]
        assert "echo 남의-훅" in commands
        ours = [c for c in commands if "hook.py" in c]
        assert ours and all("--project" not in c for c in ours), "전역 명령에는 프로젝트가 없다"
        assert all(f"hook.py\" {agent} " in c for c in ours)
        assert not apply.unwire(settings), "전역 배선은 옛 프로젝트 훅으로 치지 않는다"


def test_user_level_install_moves_the_drift_check():
    home = Path(os.environ["WIKI_USER_HOME"])
    user = home / ".claude/settings.json"
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw)
        (project / ".wiki").mkdir()
        (project / ".wiki/adapter.toml").write_text('agents=["claude"]\n', encoding="utf-8")
        try:
            assert apply.wiring_drift(project), "아무 데도 안 걸렸으면 드리프트다"
            settings = {}
            apply.configure(settings, None, None, sys.executable, "claude")
            user.parent.mkdir(parents=True, exist_ok=True)
            user.write_text(json.dumps(settings), encoding="utf-8")
            assert apply.wiring_drift(project), "전역만으로는 호스트가 직접 막는 deny 가 없다"
            (project / ".claude").mkdir()
            local = {"permissions": {"deny": ["Bash(rm -rf /*)"]}}
            assert apply.keep_denies(local) and not apply.keep_denies(local), "한 번만 더한다"
            assert "Bash(rm -rf /*)" in local["permissions"]["deny"], "사람이 쓴 규칙은 남는다"
            (project / ".claude/settings.json").write_text(json.dumps(local), encoding="utf-8")
            assert not apply.wiring_drift(project), "전역 훅 + 프로젝트 deny 면 맞다"
            old = {}
            apply.configure(old, project, None, sys.executable, "claude")
            (project / ".claude/settings.json").write_text(json.dumps(old), encoding="utf-8")
            assert apply.wiring_drift(project), "전역과 겹친 옛 프로젝트 훅은 걷으라고 말한다"
            assert apply.unwire(old) and not apply.unwire(old)
            assert old["permissions"]["deny"], "훅을 걷어도 deny 는 남는다"
        finally:
            user.unlink(missing_ok=True)


def test_user_level_claude_carries_no_machine_wide_deny():
    settings = {}
    apply.configure(settings, None, None, "C:/py.exe", "claude")
    assert not settings.get("permissions", {}).get("deny"), "전역 deny 는 위키 없는 저장소까지 막는다"
    pre = [h["command"] for g in settings["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert any(apply.runs(c, "deny.py") for c in pre), "차단 규칙은 디스패처 뒤 deny.py 로 간다"
    import deny
    blocked = deny.verdict({"tool_name": "Bash", "tool_input": {"command": "sed -i s/a/b/ x"}})
    assert blocked and blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert deny.verdict({"tool_name": "Bash", "tool_input": {"command": "git status"}}) is None


def test_a_path_named_as_data_is_not_an_old_install():
    with tempfile.TemporaryDirectory() as raw:
        top = Path(raw)
        (top / ".claude").mkdir()
        watcher = f'"python" "audit.py" --watch "{(hook.HERE / "inject.py").as_posix()}"'
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": watcher}]}]}}
        (top / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")
        assert not hook.legacy(top, "claude", "inject.py"), "남의 훅이 경로를 언급했다고 비켜서지 않는다"
        assert not apply.unwire(settings), "남의 훅은 걷지 않는다"


def needs(*hosts: str) -> None:
    missing = [h for h in hosts if not shutil.which(h)]
    if missing:
        pytest.skip(f"not installed here: {', '.join(missing)}")


def test_check_fails_when_a_named_project_gets_nothing():
    needs("claude")
    with tempfile.TemporaryDirectory() as raw:
        # An attached clone still carrying its old per-project hooks: the
        # dispatcher steps aside for them, so the global SessionStart says nothing.
        main, _tree = attached_repo(Path(raw))
        old = {}
        apply.configure(old, main, None, sys.executable, "claude")
        (main / ".claude").mkdir()
        (main / ".claude/settings.json").write_text(json.dumps(old), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(HERE / "setup_agents.py"), "--global", "--check",
             "--agent", "claude", "--project", str(main.resolve())],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        assert done.returncode == 2 and "주입하지 않았다" in done.stderr, done.stdout + done.stderr


def test_a_mistyped_project_is_refused_before_anything_is_written():
    with tempfile.TemporaryDirectory() as raw:
        typo = Path(raw) / "typo"
        # An adapter with no repository around it is not a checkout either.
        (Path(raw) / "no repo/.wiki").mkdir(parents=True)
        (Path(raw) / "no repo/.wiki/adapter.toml").write_text('agents=["claude"]\n', encoding="utf-8")
        for target in (typo, Path(raw) / "no repo"):
            done = subprocess.run(
                [sys.executable, str(HERE / "setup_agents.py"), "--global",
                 "--agent", "claude", "--project", str(target)],
                capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            assert done.returncode == 2 and "adapter.toml" in done.stderr, done.stdout + done.stderr
            assert not (target / ".claude").exists(), "차단 규칙을 남기지 않는다"
        assert not typo.exists(), "없는 경로를 만들지 않는다"
        assert not any(Path(os.environ["WIKI_USER_HOME"]).glob(".claude/settings.json")), "사용자 설정도 안 건드린다"


def test_a_missing_second_host_leaves_the_first_unwritten():
    needs("claude")
    with tempfile.TemporaryDirectory() as raw:
        # Git and Python stay findable; `codex` does not.
        bin_dir = Path(raw)
        env = {**os.environ, "PATH": os.pathsep.join(
            str(Path(shutil.which(name)).parent) for name in ("claude", "git"))}
        if shutil.which("codex", path=env["PATH"]):
            pytest.skip("codex shares a folder with claude or git here")
        done = subprocess.run(
            [sys.executable, str(HERE / "setup_agents.py"), "--global", "--agent", "both"],
            capture_output=True, text=True, encoding="utf-8", timeout=120, env=env, cwd=bin_dir,
        )
        assert done.returncode == 2 and "codex" in done.stderr, done.stdout + done.stderr
        assert not any(Path(os.environ["WIKI_USER_HOME"]).glob(".claude/settings.json")), \
            "한 호스트만 설치된 채로 끝나지 않는다"


def test_only_a_command_that_runs_the_dispatcher_is_ours():
    settings = {}
    apply.configure(settings, None, None, "C:/py.exe", "codex")
    dispatcher = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert apply.ours(dispatcher)
    watcher = f'"python" "audit.py" --watch "{(hook.HERE / "hook.py").as_posix()}"'
    assert not apply.ours(watcher), "경로를 언급만 한 남의 훅을 신뢰하지 않는다"
    assert not apply.ours('"python" "C:/elsewhere/tool/hook.py" codex inject.py'), "남의 위키 디스패처도 아니다"
    path = (hook.HERE / "hook.py").as_posix()
    for hostile in (f'echo "python" "{path}"; calc.exe',       # names it, runs something else
                    f'"python" "{path}" codex inject.py; calc',  # ours, then something else
                    f'"$(calc)" "{path}" codex inject.py',       # the shell expands the interpreter
                    f'"python" "{path}" codex not-here.py'):     # a script this wiki does not have
        assert not apply.ours(hostile), hostile
    for agent in ("claude", "codex"):
        wired = {}
        apply.configure(wired, None, None, "C:/Program Files/Python 3/python.exe", agent)
        assert all(apply.ours(h["command"]) for gs in wired["hooks"].values() for g in gs for h in g["hooks"]), \
            "우리가 쓴 명령은 공백 있는 인터프리터 경로여도 전부 우리 것이다"
    # Trust goes by identity: exactly the commands this install writes.
    mine = apply.installed("codex", sys.executable)
    commands = {c for _e, c in mine}
    assert len(mine) == 5 and all(apply.ours(c) for c in commands)
    for stranger in (f'"C:/other.exe" "{path}" codex inject.py',                 # another program
                     f'"{sys.executable}" "{path}" codex setup_agents.py --global',  # never wired
                     f'& "{sys.executable}" "{path}" codex inject.py --codex'):     # a flag we never add there
        assert stranger not in commands, stranger
    assert ("Stop", next(c for e, c in mine if e == "SessionStart")) not in mine, "이벤트까지 같아야 한다"
    narrowed = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [
        {"type": "command", "command": watcher}]}]}}
    assert not apply.restricted(narrowed), "남의 훅의 matcher 로 설치를 거절하지 않는다"


def global_install(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE / "setup_agents.py"), "--global", *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )


def test_settings_that_install_cannot_repair_are_refused_before_writing():
    needs("claude")
    home = Path(os.environ["WIKI_USER_HOME"])
    user = home / ".claude/settings.json"
    user.parent.mkdir(parents=True, exist_ok=True)
    try:
        for bad in ({"disableAllHooks": True}, None):
            if bad is None:
                # A wiki hook group narrowed to one tool.
                bad = {}
                apply.configure(bad, None, None, sys.executable, "claude")
                bad["hooks"]["PreToolUse"][0]["matcher"] = "Read"
            user.write_text(json.dumps(bad), encoding="utf-8")
            before = user.read_bytes()
            for mode in ((), ("--check",)):
                done = global_install("--agent", "claude", *mode)
                assert done.returncode == 2 and "직접 검토" in done.stderr, done.stdout + done.stderr
            assert user.read_bytes() == before, "고칠 수 없으면 아무것도 쓰지 않는다"
    finally:
        user.unlink(missing_ok=True)


def test_a_broken_later_file_leaves_the_earlier_ones_unwritten():
    needs("claude", "codex")
    home = Path(os.environ["WIKI_USER_HOME"])
    codex = home / ".codex/hooks.json"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("{ broken", encoding="utf-8")
    try:
        done = global_install("--agent", "both")
        assert done.returncode == 2, done.stdout + done.stderr
        assert not (home / ".claude/settings.json").exists(), "앞 호스트만 설치된 채 끝나지 않는다"
    finally:
        codex.unlink(missing_ok=True)
