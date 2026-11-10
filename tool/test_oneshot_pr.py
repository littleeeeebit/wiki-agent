"""oneshot step 5b: S8, the README turn, the final gate and the first pull request.

See `docs/plans/oneshot/5-screen-pr.md` ("S8"). The work turns are stand-ins; git and the
gate run for real in a temporary worktree, and GitHub is `test_specs.Remote`, so nothing
is pushed and no pull request is made.
"""

import json
import subprocess
from unittest.mock import patch

import pytest

from main import oneshot, oneshot_build, oneshot_critique as critique, oneshot_pr, oneshot_spec, pr_text, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_front_build import CONTRACT, FEATURES, FREQUENCY, FRONT, GATE, Run, adapter, git, write
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
    write(world, ".gitignore", "__pycache__/\n")
    write(world, ".wiki/adapter.toml", adapter())
    git(world, "add", "-A")
    git(world, "commit", "-m", "built")
    head = git(world, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", done=["README 와 최종 게이트"], grounds={"pages": [], "files": [], "rules": []},
                       source={"focus": "oneshot", "turn": 1.0, "plan": None}, start_head=git(world, "rev-list", "--max-parents=0", "HEAD"),
                       oneshot={
        "stage": "pr", "tests_head": head, "front_built_head": head, "contract": ["test_budget.py"], "front": FRONT,
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
    critique.decide("budget", critique.Decisions(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]}))
    stage, text = sent[-1][1:]
    assert stage == "pr" and "Windows and macOS" in text and GATE in text and "Write no CI workflow" in text
    assert o()["reading"]["r3"][0]["edited"] == []


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
    (workflow("      - run: |\n          uv sync\n          cd .\n          uv run pytest\n"), True),
    ("on:\n  push:\n    branches: [main]\n  pull_request:\njobs:\n  a:\n    steps:\n      - run: ls\n"
     "  b:\n    steps:\n      - run: uv run pytest\n", True),
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
    readme(tree, (oneshot_pr.CI, workflow(f"      - shell: pwsh\n        run: |\n          {run}")))
    gate = "uv run pytest &&\nnpm --prefix web test"
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), gate) is runs


def test_a_workflow_deleted_in_the_run_is_no_ci_and_a_committed_one_passes(finished):
    tree, sent = finished
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public", "ci": "github-actions"}})
    readme(tree, (".github/workflows/ci.yml", workflow(f"      - uses: actions/checkout@v4\n      - run: |\n          {GATE}\n")))
    assert oneshot_pr.ci_file(tree, oneshot_pr.workflows(tree).get(oneshot_pr.CI), GATE)
    git(tree, "rm", "-q", ".github/workflows/ci.yml")
    git(tree, "commit", "-m", "drop ci")
    check(tree, answer(report()))()
    assert "CI 워크플로(`.github/workflows/ci.yml`)가 없다" in sent[-1][2]


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


def test_a_push_that_times_out_stops_the_run_so_it_can_be_resumed(finished):
    tree, sent = finished
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: "Write the pull request's text."):
        check(tree, answer(report()))()
    remote = Remote()

    def timed_out(args, cwd, timeout=60):
        if args[:2] == ["git", "push"]:
            raise subprocess.TimeoutExpired(args, timeout)
        return remote(args, cwd, timeout)

    with patch.object(specs, "sh", timed_out), pytest.raises(subprocess.TimeoutExpired):
        check(tree, SUMMARIZED)
    assert o()["working"] is None and o()["stopped"]["reason"] == "pr" and "TimeoutExpired" in o()["stopped"]["detail"]
    assert not remote.created()


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


def test_a_resumed_s8_starts_again_from_its_readme_turn(finished):
    _, sent = finished
    specs.update("budget", oneshot.BRANCH, writing={"head": "a" * 40, "asked": 1})
    oneshot_spec.store("budget").stop("pr", "push 실패")
    oneshot_spec.resume("budget")
    assert sent[-1][1] == "pr" and "README.md" in sent[-1][2] and not spec().get("writing")
    assert json.loads(json.dumps(o()["stopped"])) is None
