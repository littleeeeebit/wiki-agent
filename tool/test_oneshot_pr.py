"""oneshot step 5b: S8, the README turn, the final gate and the first pull request.

See `docs/plans/oneshot/5-screen-pr.md` ("S8"). The work turns are stand-ins; git and the
gate run for real in a temporary worktree, and GitHub is `test_specs.Remote`, so nothing
is pushed and no pull request is made.
"""

import contextlib
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import android_owned
from main import github_release as gh
from main import oneshot, oneshot_build, oneshot_critique as critique, oneshot_device, oneshot_pr, oneshot_publish
from main import oneshot_spec, pr_text
from main import release, specs
from test_github_release import FakeGitHub
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_critique import on_r3
from test_oneshot_device import DEVICE
from test_oneshot_front_build import (CASES, CONTRACT, FEATURES, FREQUENCY, FRONT, GATE, RUNNER, Run, adapter, git,
                                      write)
from test_oneshot_spec import PROFILE, fenced, world  # noqa: F401 — `world` is a fixture
from test_specs import SUMMARIZED, Remote

README = "# 가계부\n\n오늘 쓴 돈을 적는다.\n\n## 설치\n\n`uv sync`\n\n## 실행\n\n`uv run python -m app`\n"


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def spec() -> dict:
    return specs.load("budget", oneshot.BRANCH)


@pytest.fixture
def finished(world):  # noqa: F811 — the imported fixture, requested by name
    write(world, "test_budget.py", CONTRACT)
    write(world, ".gitignore", "__pycache__/\nartifacts/\n")   # as the bootstrap writes it: the gate's report lands there
    write(world, ".wiki/adapter.toml", adapter())
    git(world, "add", "-A")
    git(world, "commit", "-m", "built")
    head = git(world, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", done=["README 와 최종 게이트"], grounds={"pages": [], "files": [], "rules": []},
                       source={"focus": "oneshot", "turn": 1.0, "plan": None}, start_head=git(world, "rev-list", "--max-parents=0", "HEAD"),
                       oneshot={
        "stage": "pr", "tests_head": head, "front_built_head": head, "contract": ["test_budget.py"], "front": FRONT,
        "runner": RUNNER, "gate": GATE, "cases": CASES,
        "features": FEATURES, "frequency": FREQUENCY, "first_screen": "오늘 쓴 돈을 적는다",
        "profile": {**PROFILE, "visibility": "private", "ci": "none", "os_now": "windows", "os_later": "other"},
        # As a real run reaches S8: P1's comment was applied, so the sheet holds only its later approval.
        "r1": {"round": 2, "decisions": {"P1": {"approve": True}, "R1": {"approve": True}}},
        "r2": {"round": 1, "decisions": {"S1": {"approve": True}}, "palette": "C1"},
        "r3": {"round": 1, "decisions": {"S1": {"approve": True}}}, "working": "pr",
        "reading": {"r1": [{"round": 1, "seconds": 80.0, "opened": ["흐름"], "edited": ["P1"],
                            "edits": {"P1": "응답 필드를 sum 으로"}},
                           {"round": 2, "seconds": 20.0, "opened": [], "edited": [], "edits": {}}]}})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)), \
         patch.object(specs.channels, "repo_for", lambda r: world):
        yield world, sent


def answer(*blocks) -> str:
    return "썼다.\n" + "".join(blocks)


def report() -> str:
    return fenced("done-report", [{"item": item, "pass": True, "evidence": "README"} for item in spec()["done"]])


def check(tree, final):
    return oneshot_build.checked(tree, Run(), spec(), final)


def readme(tree, *more):
    write(tree, "README.md", README)
    for name, text in more:
        write(tree, name, text)
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "readme")


# -- The README turn and the final gate -------------------------------------------------------

def test_r3s_approval_sends_the_readme_turn_for_both_systems_and_no_workflow_for_a_private_repository(finished):
    _, sent = finished
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "r3": {"round": 1, "decisions": {}}})
    critique.decide("budget", on_r3(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]}))
    stage, text = sent[-1][1:]
    assert stage == "pr" and "Windows and macOS" in text and GATE in text and "Write no CI workflow" in text
    assert o()["reading"]["r3"][0]["edited"] == []


def test_s8_refuses_an_adapter_changed_outside_release_before_its_gate_runs(finished):
    tree, sent = finished
    readme(tree, (".wiki/adapter.toml", adapter(gate=GATE + " && python -c \"open('ran.txt', 'w')\"")))
    check(tree, answer(report()))()
    assert sent[-1][1] == "pr" and "`[release]` 밖이 바뀌었다" in sent[-1][2] and "final_head" not in o()
    assert not (tree / "ran.txt").exists()   # the changed gate never ran


def test_a_stopped_s8_reads_no_adapter_and_runs_no_gate(finished):
    tree, _ = finished
    readme(tree)
    run = Run()
    run.halt.set()
    assert oneshot_pr.readied(tree, run, spec(), answer(report())) == "멈춤 — git 을 시작하지 않았다"


@pytest.mark.parametrize("change, said", [
    (lambda tree: None, "README.md"),
    (lambda tree: readme(tree, (".github/workflows/ci.yml", "on: push\n")), "CI 워크플로를 두지 않는다"),
    (lambda tree: readme(tree, ("test_budget.py", CONTRACT.replace("pass", "assert False", 1))), "바뀌었다"),
    (lambda tree: readme(tree, ("test_red.py", "def test_red():\n    assert False\n")), "최종 게이트 실패"),
])
def test_s8_refuses_to_open_without_its_readme_with_a_private_workflow_or_with_the_gate_red(finished, change, said):
    tree, sent = finished
    change(tree)
    then = check(tree, answer(report()))
    then()
    assert sent[-1][1] == "pr" and said in sent[-1][2] and o()["attempts"] == 1 and not spec().get("writing")


def workflow(steps: str) -> str:
    return f"name: ci\non: [push, pull_request]\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"


SKIPS = textwrap.indent(oneshot_pr.SKIPS, "      ")   # the summary step, as a job's step


@pytest.mark.parametrize("more", [(), ((".github/workflows/notes.txt", "later\n"),),
                                  ((".github/workflows/ci.yml", "name: ci\n"),),
                                  ((".github/workflows/ci.yml", "# jobs: TODO\n"),),
                                  ((".github/workflows/ci.yml", "jobs:\n  gate:\n    runs-on: ubuntu-latest\n"),),
                                  ((".github/workflows/ci.yml", workflow("      - run: echo hi\n")),),
                                  ((".github/workflows/ci.yml", "on: [push\n"),)])
def test_a_public_repository_needs_its_committed_ci_workflow(finished, more):
    tree, sent = finished
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public", "ci": "github-actions"}})
    readme(tree, *more)
    check(tree, answer(report()))()
    assert "CI 워크플로(`.github/workflows/ci.yml`)가 없다" in sent[-1][2]


@pytest.mark.parametrize("text, runs", [
    (workflow("      - run: |\n          # uv run pytest\n          echo done\n"), False),
    (workflow("      - run: echo uv run pytest\n"), False),
    ("on: workflow_dispatch\njobs:\n  gate:\n    steps:\n      - run: uv run pytest\n", False),
    ("on: push\njobs:\n  gate:\n    steps:\n      - run: uv run pytest\n", False),
    (workflow("      - shell: pwsh\n        run: |\n          uv run pytest --collect-only\n"), False),
    (workflow("      - run: uv run pytest tests/test_one.py\n"), False),
    (workflow("      - run: |\n          uv sync\n          cd .\n          uv run pytest\n" + SKIPS), True),
    ("on:\n  push:\n    branches: [main]\n  pull_request:\njobs:\n  a:\n    steps:\n      - run: ls\n"
     "  b:\n    steps:\n      - run: uv run pytest\n" + SKIPS, True),
    # The skip summary follows the gate in its own job, as written.
    (workflow("      - run: uv run pytest\n"), False),
    (workflow(SKIPS + "      - run: uv run pytest\n"), False),
    (workflow("      - run: uv run pytest\n" + SKIPS.replace("always()", "success()")), False),
    (workflow("      - run: uv run pytest\n" + SKIPS.replace("N[1-9]", "X[1-9]")), False),
    ("on: [push, pull_request]\njobs:\n  a:\n    steps:\n      - run: uv run pytest\n"
     "  b:\n    steps:\n" + SKIPS, False),
    # A condition on the job or on the gate's step skips both on one of the events.
    ("on: [push, pull_request]\njobs:\n  gate:\n    if: github.event_name == 'pull_request'\n    steps:\n"
     "      - run: uv run pytest\n" + SKIPS, False),
    (workflow("      - if: github.event_name == 'push'\n        run: uv run pytest\n" + SKIPS), False),
    # A job the gate's job waits on may be skipped, and a gate in the background may not be done yet.
    ("on: [push, pull_request]\njobs:\n  setup:\n    if: github.event_name == 'pull_request'\n    steps:\n"
     "      - run: ls\n  gate:\n    needs: setup\n    steps:\n      - run: uv run pytest\n" + SKIPS, False),
    (workflow("      - background: true\n        run: uv run pytest\n" + SKIPS), False),
])
def test_the_public_ci_runs_the_gate_on_every_push_and_pull_request(finished, text, runs):
    tree, _ = finished
    readme(tree, (oneshot_pr.CI, text))
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), "uv run pytest") is runs


@pytest.mark.parametrize("run, runs", [
    ("uv sync\n          uv run pytest &&\n          npm --prefix web test\n", True),
    ("uv run pytest &&\n          npm --prefix web test -- --run one\n", False),
    ("uv run pytest &&\n", False),
])
def test_a_gate_over_several_lines_runs_as_one_block(finished, run, runs):
    tree, _ = finished
    readme(tree, (oneshot_pr.CI, workflow(f"      - shell: pwsh\n        run: |\n          {run}" + SKIPS)))
    gate = "uv run pytest &&\nnpm --prefix web test"
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), gate) is runs


def test_a_workflow_deleted_in_the_run_is_no_ci_and_a_committed_one_passes(finished):
    tree, sent = finished
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public", "ci": "github-actions"}})
    readme(tree, (".github/workflows/ci.yml", workflow(f"      - uses: actions/checkout@v4\n      - run: |\n          {oneshot_pr.ci_gate(GATE)}\n" + SKIPS)))
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), GATE)
    git(tree, "rm", "-q", ".github/workflows/ci.yml")
    git(tree, "commit", "-m", "drop ci")
    check(tree, answer(report()))()
    assert "CI 워크플로(`.github/workflows/ci.yml`)가 없다" in sent[-1][2]


SKIPPED = '<testsuite><testcase classname="t" name="{name}"><skipped message="{why}"/></testcase></testsuite>'


@pytest.mark.parametrize("reports, said", [
    ({}, "JUnit 보고서(`artifacts/junit.xml`)가 없어"),
    ({"": '<testsuite><testcase classname="t" name="test_ok"/></testsuite>'}, "건너뛴 테스트는 없다"),
    ({"": '<testsuite><testcase classname="t" name="test_rate"><skipped message="N2 and N1 absent"/></testcase>'
          '<testcase classname="t" name="test_other"><skipped message="slow"/></testcase></testsuite>'},
     "- ` t test_rate `: N1, N2"),
    # A gate that changed into a folder wrote its report there. The list shows the name as it is: a code span
    # whose fence outruns its backticks, in a list item with no cell to split.
    ({"app": SKIPPED.format(name="test_values[a|b]", why="N1")}, "- ` t test_values[a|b] `: N1"),
    ({"": SKIPPED.format(name="test_values[a\\|b]", why="N1")}, r"- ` t test_values[a\|b] `: N1"),
    ({"": SKIPPED.format(name="test_values[**fast**]", why="N1")}, "- ` t test_values[**fast**] `: N1"),
    ({"": SKIPPED.format(name="test_values[a`b]", why="N1")}, "- `` t test_values[a`b] ``: N1"),
    # A need id before a Korean particle is still the id; a longer one is another.
    ({"": SKIPPED.format(name="test_rate", why="N1이 없다, N12 도, N2cache N3_fixture")}, "- ` t test_rate `: N1, N12"),
    # Two reports: which is the gate's is not known, so none is listed.
    ({"": SKIPPED.format(name="test_rate", why="N1"), "fixture": SKIPPED.format(name="test_old", why="N2")},
     "어느 것이 이 게이트의 것인지 정할 수 없다: `artifacts/junit.xml`, `fixture/artifacts/junit.xml`"),
])
def test_the_ci_summary_step_lists_the_tests_skipped_for_a_need(tmp_path, reports, said):
    for folder, report in reports.items():
        (tmp_path / folder / "artifacts").mkdir(parents=True)
        (tmp_path / folder / "artifacts" / "junit.xml").write_text(report, encoding="utf-8")
    out, step = tmp_path / "summary.md", tmp_path / "step"   # the step may run in a folder of its own
    step.mkdir()
    subprocess.run([sys.executable, "-c", oneshot_pr.SKIP_STEP["run"]], cwd=step, check=True,
                   env={**os.environ, "GITHUB_STEP_SUMMARY": str(out), "GITHUB_WORKSPACE": str(tmp_path)})
    text = out.read_text(encoding="utf-8")
    assert said in text and "test_other" not in text and "test_old" not in text


def test_the_readme_turn_of_a_public_repository_carries_the_summary_step(finished):
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public", "ci": "github-actions"}})
    text = oneshot_pr.readme_text(spec())
    # The gate runs on a runner of the system it was proven on, in that system's shell, as the server ran it.
    runner = {"win32": "windows-latest", "darwin": "macos-latest"}.get(sys.platform, "ubuntu-latest")
    assert oneshot_pr.SKIPS in text and f"a job on `{runner}`" in text and oneshot_pr.ci_gate(GATE) in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell's call operator is the Windows shell's")
def test_the_ci_gate_is_the_command_as_the_server_rendered_it(finished):
    tree, _ = finished
    gate = '"tools/run me.bat" --fast'
    readme(tree, (oneshot_pr.CI, workflow(f"      - run: |\n          & {gate}\n" + SKIPS)))
    assert oneshot_pr.ci_gate(gate) == f"& {gate}"
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), gate)


# -- The pull request -----------------------------------------------------------------------------

def test_the_gate_green_asks_for_the_text_then_opens_the_pull_request_attached_by_branch(finished):
    tree, sent = finished
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    stage, text = sent[-1][1:]
    assert stage == "pr" and text.startswith("Write the pull request's text.") and "R1 P1 (round 1): 응답 필드를 sum 으로" in text
    assert spec()["writing"]["head"] == git(tree, "rev-parse", "HEAD") and spec()["gate"]["cmd"] == GATE
    remote = Remote()
    with patch.object(specs, "sh", remote):
        then = check(tree, SUMMARIZED)
    assert remote.pushes() == 1 and remote.created() and "## 변경 이유" in remote.body
    pr = spec()["pr"]
    assert spec()["state"] == "PR #7" and pr["number"] == 7 and pr["branch"] == oneshot.BRANCH
    assert o()["stage"] == "done" and o()["pr"] == pr and then is not None
    shown = oneshot_spec.view(spec())["summary"]
    assert shown["pr"]["number"] == 7 and [g["rounds"] for g in shown["gates"]] == [2, 1, 1]
    assert shown["gates"][0]["edited"] == ["P1"] and shown["gates"][0]["reading"][0]["seconds"] == 80.0


def test_the_backends_draft_pr_left_a_draft_stops_s8_instead_of_finishing(finished):
    tree, sent = finished
    readme(tree)
    oneshot_spec.saved("budget", oneshot={"pr1": {"number": 7, "url": "https://github.com/o/proj/pull/7"}})
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()   # answers `gh pr ready` as a logged-out gh does
    with patch.object(specs, "sh", listing(remote, 7)):   # the draft, open: its text is edited, then ready asked
        assert check(tree, SUMMARIZED) is None
    assert any(c[:3] == ["gh", "pr", "ready"] for c in remote.calls) and not remote.created()
    assert o()["stage"] == "pr" and o()["stopped"]["reason"] == "pr" and "초안을 풀지 못했다" in o()["stopped"]["detail"]
    # Resumed, a new README commit's push fails: the record of the earlier publication never finishes the run.
    oneshot_spec.resume("budget")
    readme(tree, ("NOTES.md", "다시\n"))
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()
    open7 = listing(remote, 7)
    with patch.object(specs, "sh", lambda args, cwd, timeout=60: open7(args, cwd, timeout) if args[:2] != ["git", "push"]
                      else subprocess.CompletedProcess(args, 1, "", "rejected")):
        assert check(tree, SUMMARIZED) is None
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in remote.calls)
    assert o()["stage"] == "pr" and o()["stopped"]["reason"] == "pr" and "push 실패" in o()["stopped"]["detail"]


def listing(remote, *numbers):
    """`remote`, with `gh pr list` answering these open pull requests from the task's branch into `main`."""

    rows = [{"number": n, "url": f"https://github.com/o/proj/pull/{n}", "headRefName": specs.branch_of(spec()),
             "baseRefName": "main", "isCrossRepository": False} for n in numbers]
    return lambda args, cwd, timeout=60: (subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
                                          if args[:3] == ["gh", "pr", "list"] else remote(args, cwd, timeout))


@pytest.mark.parametrize("open_now", [(), (9,)])   # the draft closed, or another pull request open on the branch
def test_s8_publishes_only_to_the_draft_it_measured_never_a_replacement(finished, open_now):
    tree, sent = finished
    readme(tree)
    oneshot_spec.saved("budget", oneshot={"pr1": {"number": 7, "url": "https://github.com/o/proj/pull/7"}})
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()
    with patch.object(specs, "sh", listing(remote, *open_now)):
        assert check(tree, SUMMARIZED) is None
    # Nothing is pushed either: a push to the branch would move the pull request open on it.
    assert not remote.pushes() and not remote.created()
    assert not any(c[:3] in (["gh", "pr", "ready"], ["gh", "pr", "edit"]) for c in remote.calls)
    assert o()["stage"] == "pr" and "초안 PR #7 이 열려 있지 않다" in o()["stopped"]["detail"] and not spec().get("pr")


@pytest.mark.parametrize("open_now", [(), (9,)])
def test_a_repairs_publication_stacks_on_the_measured_draft_never_a_replacement(finished, open_now):
    tree, _ = finished
    pr1 = {"number": 7, "url": "https://github.com/o/proj/pull/7", "head": "old"}
    oneshot_spec.saved("budget", oneshot={"pr1": pr1, "built_head": git(tree, "rev-parse", "HEAD")})
    remote = Remote()
    with patch.object(specs, "sh", listing(remote, *open_now)), pytest.raises(ValueError, match="초안 PR #7 이 열려"):
        oneshot_publish.published("budget")
    assert not remote.pushes() and not remote.created() and o()["pr1"] == pr1
    with patch.object(specs, "sh", listing(remote, 7)):   # reopened: the repair goes up on it
        assert oneshot_publish.published("budget")["number"] == 7
    assert not remote.created() and o()["pr1"]["head"] == git(tree, "rev-parse", "HEAD")
    # The repository's default branch moved since: the draft keeps the base it was opened into.
    moved = listing(remote, 7)
    with patch.object(specs, "sh", lambda args, cwd, timeout=60: subprocess.CompletedProcess(args, 0, "dev\n", "")
                      if args[:3] == ["gh", "repo", "view"] else moved(args, cwd, timeout)):
        assert oneshot_publish.published("budget")["base"] == "main"
    assert not remote.created()


@pytest.mark.parametrize("change, said", [
    # an adapter gate that dropped the contract's command: refused as any change outside `[release]`, unrun
    (lambda tree: write(tree, ".wiki/adapter.toml", adapter(gate=f'"{sys.executable}" -c "pass"')), "`[release]` 밖이"),
    # the contract's command in a comment: the published gate would run only the rest
    (lambda tree: write(tree, ".wiki/adapter.toml", adapter(gate=f'"{sys.executable}" -c "pass" # {GATE}')),
     "`[release]` 밖이"),
    # a configuration the tests turn never listed, skipping every pinned case: the gate still exits 0
    (lambda tree: write(tree, "conftest.py", "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                                             "    for item in items:\n        item.add_marker(pytest.mark.skip)\n"),
     "(skipped)"),
])
def test_the_final_gate_runs_the_contracts_command_and_checks_every_pinned_case(finished, change, said):
    tree, sent = finished
    change(tree)
    readme(tree)
    check(tree, answer(report()))()
    assert sent[-1][1] == "pr" and said in sent[-1][2] and "final_head" not in o() and not spec().get("writing")


def test_a_push_github_refuses_stops_the_run_with_its_reason(finished):
    tree, sent = finished
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()
    with patch.object(specs, "sh", lambda args, cwd, timeout=60: remote(args, cwd, timeout) if args[:2] != ["git", "push"]
                      else __import__("subprocess").CompletedProcess(args, 1, "", "rejected")):
        check(tree, SUMMARIZED)
    assert o()["stopped"]["reason"] == "pr" and "push 실패" in o()["stopped"]["detail"] and not remote.created()


@pytest.mark.parametrize("slow", [["git", "push"], ["gh", "repo", "view"]])   # the push, or the draft check before it
def test_a_push_that_times_out_stops_the_run_so_it_can_be_resumed(finished, slow):
    tree, sent = finished
    readme(tree)
    if slow[0] == "gh":   # with PR #1, the draft's base is looked up before anything is pushed
        oneshot_spec.saved("budget", oneshot={"pr1": {"number": 7, "url": "https://github.com/o/proj/pull/7"}})
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()

    def timed_out(args, cwd, timeout=60):
        if args[:len(slow)] == slow:
            raise subprocess.TimeoutExpired(args, timeout)
        return remote(args, cwd, timeout)

    with patch.object(specs, "sh", timed_out), \
            (pytest.raises(subprocess.TimeoutExpired) if slow[0] == "git" else contextlib.nullcontext()):
        check(tree, SUMMARIZED)
    assert o()["working"] is None and o()["stopped"]["reason"] == "pr" and "TimeoutExpired" in o()["stopped"]["detail"]
    assert not remote.pushes() and not remote.created()


def test_a_pull_request_text_asked_again_is_the_runs_own_turn(finished):
    tree, sent = finished
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    asked = len(sent)
    then = check(tree, answer())   # no `pr-summary`: sent back once
    assert spec()["writing"]["asked"] == 2 and len(sent) == asked and o()["working"] == "pr"
    then()
    assert len(sent) == asked + 1 and sent[-1][1] == "pr" and "pr-summary" in sent[-1][2]
    assert spec()["writing"]["asked"] == 2   # still asked: the next answer is handed over again


def test_a_restart_before_the_text_is_asked_again_leaves_the_run_resumable(finished):
    tree, sent = finished
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    check(tree, answer())   # the re-ask is returned, and the server stops before it is sent
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    oneshot_spec.resume("budget")
    assert sent[-1][1] == "pr" and "README.md" in sent[-1][2] and not spec().get("writing")


# -- An Android app's S8 (`docs/plans/oneshot/7-android.md`, "R3 and S8") ---------------------------------

@pytest.fixture
def android(finished, monkeypatch):
    tree, sent = finished
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "android"}, "device": DEVICE})
    monkeypatch.setattr(oneshot_build, "toolchain", lambda spec: None)   # the gate runs as the test's own Python
    monkeypatch.setattr(oneshot_build, "built_env", lambda spec: None)
    monkeypatch.setattr(oneshot_pr, "release_checked", lambda *a: "")   # its own tests below build it
    real = oneshot_build.judged

    def building(path, *a, **k):   # the gate builds the APK, as a real Android gate does, from `built.apk`
        verdict = real(path, *a, **k)
        if not (Path(path) / DEVICE["apk"]).exists() and (out := Path(path).parent / "built.apk").exists():
            (Path(path) / DEVICE["apk"]).write_bytes(out.read_bytes())   # not `copyfile`, which tests break
        return verdict

    monkeypatch.setattr(oneshot_build, "judged", building)
    for apk in (DEVICE["apk"], DEVICE["tests"]["apk"]):   # as a real Android build leaves them: ignored outputs
        write(tree, apk, "apk")
    (tree.parent / "built.apk").write_text("apk", encoding="utf-8")   # what the gate builds
    write(tree, ".gitignore", "__pycache__/\nartifacts/\n*.apk\n")
    git(tree, "add", ".gitignore")
    git(tree, "commit", "-m", "ignore built APKs")
    head = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"tests_head": head, "front_built_head": head})
    return tree, sent


@pytest.fixture
def github(android, monkeypatch):
    """GitHub's Releases API for `me/budget`, which the task's remote names."""

    monkeypatch.setattr(release, "remote", lambda repo: "github.com/me/budget")
    return FakeGitHub("me/budget").install(monkeypatch)


def held(fake, head):
    """S8's one release for `head`, with its assets' digests."""

    rels = [r for r in fake.rels if r["tag_name"] == f"oneshot-{head[:12]}"]
    assert len(rels) == 1
    return rels[0], [gh.digest(a) for a in rels[0]["assets"]]


def made(fake):
    return [c for c in fake.calls if c[0] == "POST" and c[1] == "releases"]


def test_an_android_apps_readme_turn_names_the_apk_and_its_install_and_a_public_workflow_its_sdk(android):
    _, sent = android
    # R3 opens only after the phone round passed on this build, which R3's approval asks again.
    phone = {"status": "passed", "head": o().get("front_built_head"), "apks": o().get("apks"), "front_build": "b1"}
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "r3": {"round": 1, "decisions": {}},
                                          "front_build": "b1", "phone": phone})
    critique.decide("budget", on_r3(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]}))
    text = sent[-1][2]
    assert f"`{DEVICE['apk']}`" in text and "adb's install command" in text and "Write no CI workflow" in text
    assert f'application_id = "{DEVICE["package"]}"' in text and "RELEASE_VERSION_CODE" in text
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public", "ci": "github-actions"}})
    assert "`android-actions/setup-android`" in oneshot_pr.readme_text(spec())
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "webapp"}})
    assert "adb's" not in oneshot_pr.readme_text(spec()) and "setup-android" not in oneshot_pr.readme_text(spec())


def test_an_android_apps_apk_goes_to_a_draft_prerelease_the_pull_request_links(android, github):
    tree, sent = android
    readme(tree)
    frozen = []
    real_froze = oneshot_pr.froze
    with patch.object(oneshot_pr, "froze", lambda *a: frozen.append(real_froze(*a)) or frozen[-1]), \
            patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    head, digest = git(tree, "rev-parse", "HEAD"), android_owned.sha256(tree / DEVICE["apk"])
    rel, digests = held(github, head)
    assert rel["draft"] and rel["prerelease"] and rel["target_commitish"] == "main"   # S8's policy, unchanged
    assert [a["name"] for a in rel["assets"]] == [f"budget-{head[:12]}-debug.apk"] and digests == [digest]
    apk = o()["apk"]
    assert apk == {"head": head, "name": f"budget-{head[:12]}-debug.apk", "sha256": digest,
                   "tag": f"oneshot-{head[:12]}", "url": rel["html_url"]}
    assert not Path(frozen[-1]["path"]).exists()   # the copy it uploaded is gone
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        oneshot_pr.asked("budget")   # a resumed S8 asks for the text again: the release is not made twice
    assert len(made(github)) == 1 and len(rel["assets"]) == 1
    remote = Remote()
    with patch.object(specs, "sh", remote):
        check(tree, SUMMARIZED)
    assert f"[budget-{head[:12]}-debug.apk]({apk['url']})" in remote.body
    assert f"SHA-256 `{digest}`" in remote.body
    assert oneshot_spec.view(spec())["summary"]["apk"]["url"] == apk["url"]


def test_nothing_goes_out_while_the_secret_check_refuses(android, github):
    """The APK upload checks the exact state first; every push of the branch does too, whatever stage pushes it
    (`prerequisite_review.push`), so the pull request and a review repair's push are refused alike."""

    from main import oneshot_needs, prerequisite_review

    tree, _ = android
    readme(tree)
    with patch.object(oneshot_needs, "publishable", lambda *a: "비밀 값 [N1] 이(가) 커밋 에 들어 있다"), \
            patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
        assert not made(github) and o()["stopped"]["detail"].startswith("비밀 값 [N1]")
        remote = Remote()
        with patch.object(specs, "sh", remote):
            pushed = prerequisite_review.push(tree, spec(), ["git", "push", "origin", "HEAD"])
        assert pushed.returncode == 1 and pushed.stderr.startswith("비밀 값 [N1]") and not remote.calls


def test_the_release_holds_the_apk_the_final_gate_passed_even_when_the_tree_rebuilds_it_after(android, github):
    tree, _ = android
    readme(tree)
    passed = android_owned.sha256(tree / DEVICE["apk"])
    then = check(tree, answer(report()))
    write(tree, DEVICE["apk"], "another build after the final gate")   # Git sees nothing: the APK is ignored
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        then()
    assert held(github, o()["final_head"])[1] == [passed] and o()["apk"]["sha256"] == passed


def test_a_same_head_rebuild_the_resumed_final_gate_passes_replaces_the_released_apk(android, github):
    tree, _ = android
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
        first = o()["apk"]
        oneshot_pr.restart(spec())
        (tree.parent / "built.apk").write_text("a rebuild at the same commit", encoding="utf-8")
        rebuilt = android_owned.sha256(tree.parent / "built.apk")
        check(tree, answer(report()))()
    rel, digests = held(github, o()["final_head"])
    assert len(made(github)) == 1 and digests == [rebuilt]   # S8's own policy: the asset of that name replaced
    assert first["sha256"] != rebuilt and o()["apk"]["sha256"] == rebuilt
    assert o()["apk"]["url"] == first["url"] == rel["html_url"] and o()["apk"]["tag"] == first["tag"]


def test_an_apk_that_cannot_be_copied_still_opens_the_pull_request_and_leaves_no_partial_copy(android, github):
    tree, sent = android
    readme(tree)
    copies = []

    def broken(source, target):
        Path(target).write_bytes(b"partial")
        copies.append(Path(target))
        raise OSError("No space left on device")

    with patch.object(oneshot_pr.shutil, "copyfile", broken), \
            patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    assert sent[-1][2].startswith("Write the pull request's text.") and spec()["writing"] and o()["final_head"]
    assert "No space left on device" in o()["apk"]["error"] and not github.calls
    assert copies and not copies[0].parent.exists()
    remote = Remote()
    with patch.object(specs, "sh", remote):
        check(tree, SUMMARIZED)
    assert remote.created() and "디버그 APK 를 Release 에 올리지 못했다 — 최종 게이트가 만든 APK 를 복사하지 못했다" in remote.body


def test_a_release_whose_reply_was_lost_gets_the_apk_again_and_a_failed_one_is_said_in_the_body(android, github):
    tree, _ = android
    readme(tree)
    github.lose.add("POST releases")
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    rel, digests = held(github, o()["final_head"])   # found again by its tag: not a second draft
    assert len(made(github)) == 1 and o()["apk"]["url"] == rel["html_url"] and digests == [o()["apk"]["sha256"]]
    oneshot_spec.saved("budget", oneshot={"apk": None})
    github.rels.clear()
    github.fail["POST releases"] = [422, 422]
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        oneshot_pr.asked("budget")   # the frozen copy went with the first upload: nothing is read off the tree
    assert "고정한 APK 사본이 없거나 바뀌었다" in o()["apk"]["error"] and "url" not in o()["apk"]
    assert "디버그 APK 를 Release 에 올리지 못했다 — 최종 게이트가 고정한" in "\n".join(pr_text.apk_section(spec()))
    tree_copy = oneshot_pr.froze(spec(), o()["final_head"], {})
    oneshot_spec.saved("budget", oneshot={"apk_frozen": tree_copy})
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        oneshot_pr.asked("budget")
    assert "HTTP 422" in o()["apk"]["error"] and not Path(tree_copy["path"]).exists() and not github.rels


@pytest.mark.parametrize("trouble", ["502", "lost", "last lost"])
def test_a_transient_upload_failure_is_tried_again_and_a_landed_one_is_not_sent_twice(android, github, trouble):
    tree, _ = android
    readme(tree)
    if trouble == "502":
        github.fail["POST releases/{id}/assets"] = [502]
    elif trouble == "lost":
        github.lose.add("POST releases/{id}/assets")
    else:   # two 502s, then the last permitted upload lands and only its answer is lost
        github.fail["POST releases/{id}/assets"] = [502] * (oneshot_pr.TRIES - 1)
        github.lose.add("POST releases/{id}/assets")
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    rel, digests = held(github, o()["final_head"])
    uploads = [c for c in github.calls if c[0] == "POST" and c[1].endswith("/assets")]
    assert digests == [o()["apk"]["sha256"]] and "error" not in o()["apk"]
    assert len(uploads) == {"502": 2, "lost": 1, "last lost": oneshot_pr.TRIES}[trouble]


def test_only_the_apk_the_final_gate_makes_is_frozen_and_a_failed_later_check_keeps_no_copy(android):
    """An APK an earlier build left is gone before the gate: a gate that makes none is sent back, and a committed
    one is refused untouched. A pinned check that raises after the freeze leaves no copy behind."""

    tree, sent = android
    readme(tree)
    (tree.parent / "built.apk").unlink()   # this gate builds nothing; the earlier build's APK is still there
    check(tree, answer(report()))()
    assert sent[-1][1] == "pr" and f"`{DEVICE['apk']}`" in sent[-1][2] and "final_head" not in o()
    write(tree, DEVICE["apk"], "apk")
    git(tree, "add", "-f", DEVICE["apk"])
    git(tree, "commit", "-m", "a committed build output")
    assert "커밋돼 있다" in oneshot_device.cleared(tree, DEVICE) and (tree / DEVICE["apk"]).is_file()
    if os.name == "nt":   # another casing of the same file: Git's pathspec misses it, Windows opens it
        aliased = {**DEVICE, "apk": DEVICE["apk"].upper().replace(".APK", ".apk")}
        assert "커밋돼 있다" in oneshot_device.cleared(tree, aliased) and (tree / DEVICE["apk"]).is_file()
    git(tree, "rm", "-q", "--cached", DEVICE["apk"])   # its removal staged: still in HEAD, nothing deleted under it
    assert "커밋 안 된 변경" in oneshot_device.cleared(tree, DEVICE) and (tree / DEVICE["apk"]).is_file()
    git(tree, "commit", "-m", "not tracked")
    write(tree, ".wiki/decisions/a-record.md", "the start hook's record")   # the app's own: not work, as at the gate
    assert oneshot_device.cleared(tree, DEVICE) == "" and not (tree / DEVICE["apk"]).exists()
    (tree / ".wiki/decisions/a-record.md").unlink()
    # Only an output Git ignores, outside the app's record folders, is ever removed.
    for kept in ("app/kept.APK.txt", ".wiki/decisions/x.apk"):
        write(tree, kept, "not a build output")
        assert "무시하는 빌드 결과가 아니다" in oneshot_device.cleared(tree, {**DEVICE, "apk": kept}) or \
            "커밋 안 된 변경" in oneshot_device.cleared(tree, {**DEVICE, "apk": kept})
        assert (tree / kept).is_file()
        (tree / kept).unlink()
    (tree.parent / "built.apk").write_text("apk", encoding="utf-8")
    with patch.object(oneshot_build, "held", lambda *a: (_ for _ in ()).throw(ValueError("no report"))):
        check(tree, answer(report()))()
    release_dir = oneshot_spec.store("budget").folder / "release"
    assert "final_head" not in o() and not [p for p in release_dir.rglob("*") if p.is_file()]


RELEASE_CHECKED = oneshot_pr.release_checked
RELEASE_TABLE = ('\n[release]\nversion_source = "version.json"\n\n[release.android]\nbuild = "gradlew.bat assembleRelease"\n'
                 f'artifact = "app/release.apk"\napplication_id = "{DEVICE["package"]}"\nabi = "none"\n')


@pytest.fixture
def releasing(android, tmp_path, monkeypatch):
    """The README turn's `[release]` table committed, and a release build whose APK reads as `seen`."""

    tree, _ = android
    write(tree, ".wiki/adapter.toml", (tree / ".wiki/adapter.toml").read_text(encoding="utf-8") + RELEASE_TABLE)
    write(tree, "version.json", '{"version": "0.1.0"}')
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "release table")
    seen, built = {"package": DEVICE["package"], "code": 1001, "name": "0.1.1", "abis": [], "debuggable": False}, []

    def build(src, android, version, kit, halt, drop=frozenset()):
        assert release._stop.halt is halt   # under the stop's scope, the checkout's clone before it included
        built.append((git(src, "rev-parse", "HEAD"), git(src, "status", "--porcelain"), version, android["artifact"]))
        return src / android["artifact"]

    monkeypatch.setattr(release, "WORK", tmp_path / "release-work")
    monkeypatch.setattr(release, "tools", lambda cfg: {"aapt2": "aapt2"} if cfg["android"]["abi"] == "none" else {})
    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(release, "environment", lambda kit: {})
    monkeypatch.setattr(release, "run", lambda argv, env=None, timeout=300: "badging")
    monkeypatch.setattr(release, "badging", lambda text: seen)
    return tree, seen, built


def test_s8_builds_the_release_table_once_from_a_clean_checkout_and_checks_it_as_a_release_does(releasing):
    tree, seen, built = releasing
    head = git(tree, "rev-parse", "HEAD")
    assert RELEASE_CHECKED(tree, spec(), head, threading.Event()) == ""
    # The patch after the seed, on the committed HEAD: a build that kept the source's 0.1.0 would fail.
    assert built == [(head, "", "0.1.1", "app/release.apk")]
    assert list((release.WORK / tree.name).iterdir()) == []   # the checkout went with the check
    seen.update(debuggable=True, abis=["arm64-v8a"])
    why = RELEASE_CHECKED(tree, spec(), head, threading.Event())
    assert "디버그 빌드다" in why and "ABI ['arm64-v8a']" in why
    seen.update(debuggable=False, abis=[], name="0.1.0", code=1000)   # the seed's, whatever it was given
    assert "versionName '0.1.0'" in RELEASE_CHECKED(tree, spec(), head, threading.Event())


def test_s8_refuses_any_adapter_change_outside_the_release_table(releasing):
    tree = releasing[0]
    adapter = tree / ".wiki/adapter.toml"
    write(tree, ".wiki/adapter.toml", "agents = []\n" + adapter.read_text(encoding="utf-8"))
    git(tree, "commit", "-qam", "no hosts")
    assert "`[release]` 밖이 바뀌었다" in RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event())
    assert releasing[2] == []


def test_a_seed_at_androids_patch_limit_is_built_as_the_next_minor(releasing):
    tree, seen, built = releasing
    write(tree, "version.json", '{"version": "0.1.999"}')
    git(tree, "commit", "-qam", "the last patch")
    seen.update(name="0.2.0", code=2000)
    assert RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event()) == ""
    assert built[-1][2] == "0.2.0"


def test_a_stop_before_the_check_reads_the_commit_runs_nothing(releasing, monkeypatch):
    tree, _, built = releasing
    head, launched, the_spec = git(tree, "rev-parse", "HEAD"), [], spec()
    monkeypatch.setattr(release.subprocess, "Popen", lambda *a, **k: launched.append(a) or (_ for _ in ()).throw(OSError))
    halt = threading.Event()
    halt.set()
    assert "`[release.android]` 의 정식 빌드 —" in RELEASE_CHECKED(tree, the_spec, head, halt)
    assert launched == [] and built == []


def test_s8_refuses_an_installer_table_for_an_app_with_no_pc_app(releasing):
    tree = releasing[0]
    write(tree, ".wiki/adapter.toml", (tree / ".wiki/adapter.toml").read_text(encoding="utf-8") +
          '\n[release.desktop]\nbuild = "b"\nartifact = "../outside.exe"\nexe = "a.exe"\nproduct_name = "p"\n'
          'publisher = "q"\n')
    git(tree, "commit", "-qam", "an installer too")
    assert "`[release.desktop]`" in RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event())
    assert releasing[2] == []


def test_a_stop_during_the_release_build_ends_the_check_and_still_removes_its_checkout(releasing, monkeypatch):
    tree = releasing[0]

    def stopped(src, android, version, kit, halt, drop=frozenset()):
        raise release.Stopped("멈췄다")

    monkeypatch.setattr(release, "build", stopped)
    assert RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event()).endswith("멈췄다")
    assert list((release.WORK / tree.name).iterdir()) == []


def test_only_an_android_apps_readme_turn_may_write_the_adapter(android):
    grant = oneshot_build.GRANTS[oneshot_pr.STAGE]
    assert grant(spec()) and not grant({**spec(), "writing": {"head": "h", "asked": 1}})
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "webapp"}})
    assert not grant(spec())


def test_s8_sends_back_a_missing_or_wrong_release_table_and_a_failed_release_build(releasing, monkeypatch):
    tree = releasing[0]
    first = git(tree, "rev-parse", "HEAD~1")
    assert "`[release]` 표 —" in RELEASE_CHECKED(tree, spec(), first, threading.Event())   # before the table
    write(tree, ".wiki/adapter.toml", (tree / ".wiki/adapter.toml").read_text(encoding="utf-8")
          .replace(DEVICE["package"], "com.example.other"))
    git(tree, "commit", "-qam", "another app")
    assert "`application_id` 'com.example.other'" in RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"),
                                                                       threading.Event())
    git(tree, "revert", "--no-edit", "HEAD")

    def failed(src, *a, **k):
        raise release.BuildFailed("빌드 실패 — 1 로 끝났다")

    monkeypatch.setattr(release, "build", failed)
    why = RELEASE_CHECKED(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event())
    assert why == "`[release.android]` 의 정식 빌드 — 빌드 실패 — 1 로 끝났다"
    assert list((release.WORK / tree.name).iterdir()) == []


def test_an_android_app_whose_release_table_fails_is_sent_back_before_its_apk_is_frozen(android, monkeypatch):
    tree, sent = android
    monkeypatch.setattr(oneshot_pr, "release_checked", RELEASE_CHECKED)
    readme(tree)
    check(tree, answer(report()))()
    assert sent[-1][1] == "pr" and "`[release]` 표" in sent[-1][2] and "final_head" not in o() and "apk_frozen" not in o()


def test_a_resumed_s8_starts_again_from_its_readme_turn(finished):
    _, sent = finished
    specs.update("budget", oneshot.BRANCH, writing={"head": "a" * 40, "asked": 1})
    oneshot_spec.store("budget").stop("pr", "push 실패")
    oneshot_spec.resume("budget")
    assert sent[-1][1] == "pr" and "README.md" in sent[-1][2] and not spec().get("writing")
    assert json.loads(json.dumps(o()["stopped"])) is None
