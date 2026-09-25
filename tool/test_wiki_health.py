"""Reproduce the blind spots of the meters, the wiring and the checks in a
throwaway repository."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import apply
from wiki import match
import lint
import repo_lint
import trigger_audit

TOOL = Path(__file__).resolve().parent


def run(script, *args, payload=None, env=None):
    return subprocess.run(
        [sys.executable, str(TOOL / script), *map(str, args)],
        input=payload, capture_output=True, timeout=30,
        env={**os.environ, **(env or {})},
    )


def test_wiring():
    assert "tree" not in apply.unfilled("example"), "git 리비전 문법은 슬롯이 아니다"
    assert "gate_cmd" in apply.unfilled("__health_missing_adapter__"), "선언된 실제 슬롯 누락은 잡아야 한다"
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        agents = ("claude", "codex")
        assert apply.wiring_drift(project, agents)
        for agent, name in (("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")):
            settings = {"hooks": {"SessionEnd": [{"hooks": [{"command": "keep"}]}]}}
            assert apply.configure(settings, project, "example", sys.executable, agent)
            assert not apply.configure(settings, project, "example", sys.executable, agent)
            path = project / name
            path.parent.mkdir()
            path.write_text(json.dumps(settings), encoding="utf-8")
        assert not apply.wiring_drift(project, agents)
        assert not repo_lint.check(project)
        path = project / ".claude/settings.json"
        original = path.read_text(encoding="utf-8")
        for change in ("deny", "pretool", "timeout", "matcher"):
            settings = json.loads(original)
            if change == "deny":
                settings["permissions"]["deny"].pop()
            elif change == "pretool":
                settings["hooks"]["PreToolUse"].pop()
            elif change == "timeout":
                settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] = 1
            else:
                settings["hooks"]["UserPromptSubmit"][0]["matcher"] = "never"
            path.write_text(json.dumps(settings), encoding="utf-8")
            assert apply.wiring_drift(project, agents), change
            assert any(k == "훅 배선 드리프트" for k, _m in repo_lint.check(project))
            if change == "matcher":
                assert run("apply.py", "--project", project, "--adapter", "example", "--check").returncode == 1
        path.write_text(original, encoding="utf-8")
        assert run("apply.py", "--project", project, "--adapter", "example", "--check").returncode == 0

        # Somebody else's UserPromptSubmit hook sitting in front does not make
        # the wiring wrong. If the drift check picks its own hook by name,
        # their command becomes `commands[0]`, the first quoted token in it
        # reads as "the installed interpreter", and sound wiring reports
        # drift — which is then what fails the gate.
        theirs = project / "other"
        theirs.mkdir()
        (theirs / "inject.py").write_text("# 남의 것\n", encoding="utf-8")
        settings = json.loads(original)
        settings["hooks"]["UserPromptSubmit"].insert(0, {"hooks": [{
            "type": "command",
            "command": f'"{sys.executable}" "{(theirs / "inject.py").as_posix()}"',
        }]})
        path.write_text(json.dumps(settings), encoding="utf-8")
        assert not apply.wiring_drift(project, agents), "남의 훅이 앞에 있다고 드리프트가 아니다"

        # When that foreign hook points at a file that is not there, it is
        # reported — never removed. The command alone cannot say whether it is
        # where the wiki used to live or a share that is offline for a minute.
        (theirs / "inject.py").unlink()
        assert apply.wiring_drift(project, agents), "없는 파일을 가리키는 훅은 말해야 한다"
        path.write_text(original, encoding="utf-8")
        path.unlink()
        assert run("apply.py", "--project", project, "--adapter", "example", "--check").returncode == 1
        assert not path.exists(), "검사가 설정을 썼다"
        (project / ".codex/hooks.json").unlink()
        (project / "adapters").mkdir()
        (project / "adapters" / f"{project.name}.toml").write_text('agents=["claude","codex"]', encoding="utf-8")
        previous = apply.WIKI
        try:
            apply.WIKI = project
            assert apply.wiring_drift(project), "설정 파일 전체가 사라져도 기대 에이전트는 남는다"
        finally:
            apply.WIKI = previous


def test_measurement():
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "adapters").mkdir()
        (project / "adapters/x.toml").write_text(
            '[slots]\nrule_budget=200\nrepo_budget=250\nvalue="치환된 값"\n', encoding="utf-8",
        )
        (project / "craft").mkdir()
        (project / "craft/rule.md").write_text(
            '---\nseverity: contract\ntriggers: [시험]\n---\n# 규칙\n규칙. {value}\n' + "긴 본문" * 100,
            encoding="utf-8",
        )
        (project / ".wiki/decisions").mkdir(parents=True)
        for n in range(5):
            (project / f".wiki/decisions/2026-01-0{n+1}.md").write_text(
                f'---\nseverity: contract\ntriggers: [시험]\n---\n# 결정 {n}\n왜. 이유다. 자세한 이유.',
                encoding="utf-8",
            )
        original = match.WIKI
        try:
            match.WIKI = project
            available = match.pages("x", project)
            measured = trigger_audit.measure("시험 🐋", available, 200, 250)
        finally:
            match.WIKI = original
        assert measured["repo"] > 0 and len(measured["names"]) == 6
        response = run(
            "inject.py", "--adapter", "x", "--project", project,
            payload=json.dumps({"prompt": "시험 🐋", "session_id": "health-test"}, ensure_ascii=False).encode("utf-8"),
            # Measured with the translation off. `trigger_audit.measure` is
            # an offline replay and cannot translate while the hook does, so
            # leaving it on turns this assertion from "are the two in the same
            # unit" into "did the translation run".
            # Removing the key is not enough — the cache answers first.
            env={"WIKI_ROOT": str(project), "PYTHONIOENCODING": "cp949",
                 "LOCALAPPDATA": tmp, "GEMINI_API_KEY": "",
                 "TRANSLATE_ENV": str(project / "absent.env"),
                 "TRANSLATE_CACHE": str(project / "translate-cache.sqlite3")},
        )
        assert response.returncode == 0 and not response.stderr, response.stderr
        output = json.loads(response.stdout)
        assert "치환된 값" in output["hookSpecificOutput"]["additionalContext"]
        row = json.loads((project / ".wiki/trajectory.jsonl").read_text(encoding="utf-8"))
        assert row["cost"] == measured["rule"] + measured["repo"]
        assert row["injected"] == measured["names"]
        census = project / "census-a.jsonl"
        census.write_text('{"text":"시험"}\n', encoding="utf-8")
        assert trigger_audit.census_paths([str(project / "census-*.jsonl")]) == [census]
        audit = run("trigger_audit.py", project / "census-*.jsonl", "--samples", "0")
        assert audit.returncode == 0 and "| repo | 미측정" in audit.stdout.decode("utf-8")


def test_lint_sees_itself_and_malformed_pages():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tool").mkdir()
        for name in ("lint.py", "repo_lint.py"):
            source = (TOOL / name).read_text(encoding="utf-8")
            (root / "tool" / name).write_text(source, encoding="utf-8")
            assert name not in lint.fragile_tools(root)
            (root / "tool" / name).write_text(
                source.replace('sys.stdout.reconfigure(encoding="utf-8")', 'sys.stderr.reconfigure(encoding="utf-8")'),
                encoding="utf-8",
            )
            assert name in lint.fragile_tools(root)
        source = (TOOL / "session_state.py").read_text(encoding="utf-8")
        path = root / "tool/session_state.py"
        path.write_text(source, encoding="utf-8")
        assert not lint.fragile_io(root)
        assert not lint.missing_hook_guards(root, {})
        path.write_text(source.replace('    sys.stdin.reconfigure(encoding="utf-8")', ''), encoding="utf-8")
        assert any("stdin" in m for _k, m in lint.fragile_io(root))
        path.write_text(source.replace('errors="replace"', 'errors="strict"'), encoding="utf-8")
        assert any("자식 출력" in m for _k, m in lint.fragile_io(root))
        path.write_text(source.replace("except Exception as _error:", "except ValueError as _error:"), encoding="utf-8")
        assert lint.missing_hook_guards(root, {})
        (root / "craft").mkdir()
        (root / ".wiki/decisions").mkdir(parents=True)
        for broken in ('---\nseverity: contract\ntriggers: ["\\s"]\n---\n# 깨짐',
                       '---\nseverity: contract\ntriggers: ["["]\n---\n# 정규식'):
            (root / "craft/broken.md").write_text(broken, encoding="utf-8")
            (root / ".wiki/decisions/broken.md").write_text(broken, encoding="utf-8")
            assert any(k == "페이지 형식 오류" for k, _m in lint.check(root)[2])
            assert any(k == "페이지 형식 오류" for k, _m in repo_lint.check(root))


def test_synthetic_trigger_cases():
    # These examples are authored fixtures, not collected user conversations.
    available = match.pages()
    cases = [
        ("리뷰 루프를 실행해 주세요", "operator/codex-review-loop", True),
        ("오늘 날씨를 알려 주세요", "operator/codex-review-loop", False),
        ("결과 파일을 감시해 주세요", "craft/pick-up-async-results", True),
        ("화면 레이아웃을 검토해 주세요", "craft/screen-follows-the-purpose", True),
        # Saying they are leaving for over an hour, in each way the rule
        # names (review round 2 of the step-8 PR: the hours case was missed).
        ("두 시간 후에 돌아올게", "operator/compact-before-idle", True),
        ("3시간 뒤에 복귀한다", "operator/compact-before-idle", True),
        ("점심 먹고 올게", "operator/compact-before-idle", True),
        ("내일 이어서 하자", "operator/compact-before-idle", True),
        ("잠깐 자리 비울게", "operator/compact-before-idle", True),
        ("오늘은 퇴근한다", "operator/compact-before-idle", True),
        ("이 테스트는 두 시간 걸린다", "operator/compact-before-idle", False),
        ("diskpart 로 compact vdisk 를 돌려라", "operator/compact-before-idle", False),
        ("<task-notification> 두 시간 후에 돌아올게", "operator/compact-before-idle", False),
    ]
    for text, page, expected in cases:
        names = trigger_audit.measure(text, available)["names"]
        assert (page in names) == expected, (text, page)


def test_gate_ignores_only_slot_differences():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "adapters").mkdir()
        for name in ("a", "b"):
            (root / f"adapters/{name}.toml").write_text(f'[slots]\nvalue="{name}"', encoding="utf-8")
        assert run("lint.py", "--wiki", root).returncode == 1
        assert run("lint.py", "--wiki", root, "--check").returncode == 0
        (root / "craft").mkdir()
        (root / "craft/broken.md").write_text('---\ntriggers: ["\\s"]\n---', encoding="utf-8")
        assert run("lint.py", "--wiki", root, "--check").returncode == 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, test in sorted(list(globals().items())):
        if name.startswith("test_"):
            test()
            print(f"ok  {name}")
