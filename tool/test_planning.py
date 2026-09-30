"""The Plan action (`docs/plans/reliability/7-planner.md`): one request makes
one plan spec and worktree, planner A researches and drafts through a fake
host, the server writes only the new plan folder, the pull request goes up
once, and A hands off to the reviser and the review cell the request named.

The host is a stand-in behind `planning.ChatSession`; GitHub and the push are
stand-ins behind `specs.sh`. Git itself runs.
"""

import json
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent.chat_session import Event
from main import loop, planning, specs, work
from test_main import client, no_machine_settings, until  # noqa: F401 — the fixture is autouse
from test_specs import KICKED, Remote, repo  # noqa: F401 — `repo` is a fixture

USAGE = {"in": 10, "out": 5}
SOURCE = {"id": "S1", "title": "Workflow patterns", "url": "https://example.com/workflows", "retrieved": "2026-09-30",
          "locator": "Evaluator-optimizer", "fragment": "Separate generation from evaluation",
          "applicability": "planner and reviewer roles", "counterevidence": "", "rejected": "one agent does all",
          "validation": "the reviewer's session differs from the planner's", "claims": ["C1"]}
OUTLINE = {"requirements": [{"id": "R1", "text": "an explicit entry"}, {"id": "R2", "text": "publish once"}],
           "stages": [{"n": 1, "slug": "entry", "title": "Entry", "depends_on": [], "requirement_ids": ["R1"]},
                      {"n": 2, "slug": "publish", "title": "Publish", "depends_on": [1], "requirement_ids": ["R2"]}]}


class Host:
    """A host session that answers each turn with the next of `replies`: a
    string, or `(text, web, tokens)`, or a callable of the turn's text and stop."""

    replies: list = []
    made: list = []

    def __init__(self, path, tools="", system="", model="", effort="", **rest):
        self.path, self.tools, self.system, self.model, self.effort = Path(path), tools, system, model, effort
        self.id, self.session_id, self.parent_id, self.is_codex = uuid.uuid4().hex, None, None, False
        self.heard, self.closed = [], False
        Host.made.append(self)

    def say(self, text, halt=None):
        self.heard.append(text)
        reply = Host.replies.pop(0) if Host.replies else "nothing more"
        if callable(reply):
            reply = reply(text, halt)
        said, web, tokens = reply if isinstance(reply, tuple) else (reply, True, USAGE)
        if web:
            yield Event("tool", "WebSearch · planner roles", {"tool": "WebSearch"}, self.id)
        yield Event("tool", "Read · docs", {"tool": "Read"}, self.id)
        self.session_id = "cli-plan"
        yield Event("done", said, {"session_id": "cli-plan", "error": False,
                                   **({"tokens": tokens} if tokens else {})}, self.id)

    def stop(self, halt):
        pass

    def close(self):
        self.closed = True


class GitHub(Remote):
    """`Remote`, with the pull requests it made listed again by `--head`."""

    def __init__(self):
        super().__init__()
        self.prs, self.push_fails, self.create_times_out = [], False, False

    def __call__(self, args, cwd, timeout=60):
        if args[:2] == ["git", "push"] and self.push_fails:
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "remote rejected\n")
        if args[:3] == ["gh", "pr", "list"] and "--head" in args:
            self.calls.append(args)
            head, base = args[args.index("--head") + 1], args[args.index("--base") + 1]
            return subprocess.CompletedProcess(args, 0, json.dumps([p for p in self.prs if (
                p["headRefName"], p["baseRefName"]) == (head, base)]), "")
        if args[:3] == ["gh", "pr", "create"]:
            self.prs.append({"number": 7, "url": "https://github.com/o/proj/pull/7", "isCrossRepository": False,
                             "headRefName": args[args.index("--head") + 1], "baseRefName": args[args.index("--base") + 1]})
            if self.create_times_out:
                self.calls.append(args)
                raise subprocess.TimeoutExpired(args, timeout)
        return super().__call__(args, cwd, timeout)

    def creates(self) -> int:
        return sum(1 for c in self.calls if c[:3] == ["gh", "pr", "create"])


@pytest.fixture
def checkout(request) -> Path:
    """`test_specs.repo`: the selected project `proj`, its original checkout."""

    return request.getfixturevalue("repo")


@pytest.fixture(autouse=True)
def fresh_hosts():
    Host.replies, Host.made = [], []
    yield
    with planning._lock:
        running = list(planning._workers.values())
    for w in running:
        w.cancel()
        w.thread.join(10)


def request(**extra) -> dict:
    return {"request_id": "req-0001-abcd", "goal": "Add a research-backed planning workflow", "context": "PR 7",
            "slug": "planner-x", "stages": 2,
            "roles": {"planner": {"model": "claude-opus-5-5", "effort": "high"},
                      "reviser": {"model": "codex:gpt-6.1-sol", "effort": "high"},
                      "reviewer": {"model": "claude-sonnet-5-5", "effort": "medium"}},
            "limits": {"seconds": 600, "calls": 12, "tokens": 100_000}, **extra}


def root_of(text: str) -> str:
    return re.search(r"The plan folder is `([^`]+)/`", text)[1]


def block(name: str, value) -> str:
    return f"```{name}\n" + json.dumps(value, ensure_ascii=False) + "\n```"


def file_block(path: str, content: str, reqs: list[str], sources: list[str]) -> str:
    return block("plan-file", {"path": path, "content": content, "requirement_ids": reqs, "source_ids": sources})


OVERVIEW_MD = ("# Planner\n\n## Problem\n\nR1 and R2.\n\n## Constraints\n\nRead-only planner.\n\n## Decisions\n\n"
               "Adopt S1.\n\n## Stages\n\n| 1 | [Entry](1-entry.md) |\n| 2 | [Publish](./2-publish.md) |\n\n"
               "## Sources\n\n- S1: https://example.com/workflows\n")


def stage_md(rid: str, source: str = "S1") -> str:
    return (f"# Stage\n\n## Requirements\n\n{rid}, per {source}.\n\n## Entry points\n\nProposed.\n\n## Contracts\n\n"
            "x\n\n## Errors\n\nx\n\n## Edits\n\n1. x\n\n## Tests\n\n`pytest`\n\n## Rollback\n\nRevert. "
            "See [the overview](0-overview.md) and [a file](../../../a.txt).\n")


def sources(text, halt):
    return "Researched.\n\n" + block("plan-sources", [SOURCE])


def outline(text, halt):
    root = root_of(text)
    return block("plan-outline", OUTLINE) + "\n\n" + file_block(f"{root}/0-overview.md", OVERVIEW_MD, ["R1", "R2"], ["S1"])


def stage(n: int, source: str = "S1"):
    slug, rid = ("entry", "R1") if n == 1 else ("publish", "R2")
    return lambda text, halt: file_block(f"{root_of(text)}/{n}-{slug}.md", stage_md(rid, source), [rid], [source])


def git(path, *args) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def settled(sid: str, *phases: str) -> dict:
    """The spec once its worker let go — and, at `handoff`, once the hand-off
    that runs after that is written too."""

    def done() -> dict | None:
        spec = specs.load("proj", sid)
        p = (spec or {}).get("planning") or {}
        ended = p.get("phase") in phases and ("proj", sid) not in planning._workers
        return spec if ended and (p["phase"] != "handoff" or p.get("handed")) else None

    # A read can miss the file while a save swaps it in: the spec that answered is kept.
    seen: list = []
    until(lambda: seen.append(done()) or seen[-1] is not None)
    return seen[-1]


def planned_folder(spec: dict) -> set[str]:
    path = Path(spec["worktree"])
    return set(git(path, "-c", "core.quotepath=off", "diff", "--name-only",
                   spec["planning"]["base_head"], "HEAD").splitlines())


# -- one run ------------------------------------------------------------------------


def test_one_run_publishes_a_two_stage_plan_once_and_hands_off(checkout):
    web = client()
    remote = GitHub()
    Host.replies = [sources, outline, stage(1), stage(2)]
    before = git(checkout, "status", "--porcelain")
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        answer = web.post("/api/plans", json=request())
        assert answer.status_code == 200, answer.text
        sid = answer.json()["id"]
        assert sid == "planner-x" and answer.json()["review_profile"] == "plan"
        spec = settled(sid, "handoff")
        until(lambda: KICKED)

        p, path = spec["planning"], Path(spec["worktree"])
        root = p["artifact_root"]
        assert root == "docs/plans/planner-x" and spec["artifact_root"] == root
        assert spec["state"] == "PR #7" and spec["pr"]["number"] == 7 and spec["pr"]["head"] == git(path, "rev-parse", "HEAD")
        assert remote.creates() == 1 and p["publication"] == {"head": spec["pr"]["head"],
                                                              "pr": {"number": 7, "url": "https://github.com/o/proj/pull/7"}}
        assert planned_folder(spec) == {f"{root}/0-overview.md", f"{root}/1-entry.md", f"{root}/2-publish.md"}, \
            "새 계획 폴더 밖은 한 줄도 쓰지 않는다"
        assert not git(path, "status", "--porcelain")
        assert not (checkout / "docs").exists() and git(checkout, "status", "--porcelain") == before, \
            "원본 체크아웃은 그대로다"
        assert (path / root / "1-entry.md").read_bytes().startswith(b"# Stage"), "BOM 없는 UTF-8"
        assert p["source_manifest"][0]["kind"] == "web" and p["web"]["observed"] == 1
        assert [s["n"] for s in p["outline"]["stages"]] == [1, 2]
        assert (p["spent"]["calls"], p["spent"]["tokens"], p["spent"]["tools"]) == (4, 60, 8)

        # A: read-only with web tools, one session for the run, closed before the hand-off.
        [planner] = Host.made
        assert planner.tools == planning.PLANNER_TOOLS and "Bash" not in planner.tools and "Edit" not in planner.tools
        assert planner.closed and p["sessions"]["planner"]["cell"] == planner.id
        assert (planner.model, planner.effort) == ("claude-opus-5-5", "high")
        # The reviser works the pull request, the reviewer checks it; neither is A.
        assert spec["cell"] == {"model": "codex:gpt-6.1-sol", "effort": "high"}
        assert spec["reviewer"] == {"model": "claude-sonnet-5-5", "effort": "medium"}
        assert [k for k, _ in KICKED] == [sid] and p.get("handed")
        rows = work.recall(path)
        assert rows[-1]["role"] == "context" and not any(r.get("session_id") for r in rows), \
            "이 작업트리의 다음 세션이 A 의 대화를 잇지 않는다"
        assert "https://example.com/workflows" in remote.body and "## 변경 요약" in remote.body

        # The same key and input is the same plan; other input under it is a conflict.
        again = web.post("/api/plans", json=request())
        assert again.status_code == 200 and again.json()["id"] == sid
        assert web.post("/api/plans", json=request(goal="Something else")).status_code == 409
        assert remote.creates() == 1 and len(specs.listing("proj")) == 1


def test_the_review_cell_is_the_named_reviewer_and_never_a_planner_session(checkout):
    remote = GitHub()
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "handoff")
    with patch.object(loop, "ChatSession", Host):
        cell = loop.cell(spec, Path(spec["worktree"]))
        try:
            assert (cell.model, cell.effort, cell.tools) == ("claude-sonnet-5-5", "medium", loop.REVIEW_TOOLS)
            assert cell.id != spec["planning"]["sessions"]["planner"]["cell"]
        finally:
            loop.close_cell("proj", 7)
    shown = "\n".join(loop.profiled(spec, "plan", "a" * 40, "main", "b" * 40))
    assert "- R1: an explicit entry" in shown and "- S1: Workflow patterns · https://example.com/workflows" in shown
    assert "`docs/plans/planner-x/`" in shown


# -- what a request must carry -----------------------------------------------------------


@pytest.mark.parametrize("limits", [{"seconds": 0, "calls": 3, "tokens": 10}, {"seconds": 60, "calls": -1, "tokens": 10},
                                    {"seconds": 60, "calls": 3, "tokens": 0}, {"seconds": 60, "calls": 3}])
def test_a_plan_without_positive_limits_does_not_start(checkout, limits):
    answer = client().post("/api/plans", json=request(limits=limits))
    assert answer.status_code in (400, 422)
    assert specs.listing("proj") == [] and not (checkout.parent / "proj-worktrees").exists()


def test_a_question_waits_for_its_answers_and_only_the_current_ones(checkout):
    web = client()
    asked = [{"id": "q1", "question": "Which scope?", "options": [{"label": "Narrow", "note": "cheap"},
                                                                    {"label": "Wide", "note": "costly"}]}]
    Host.replies = [lambda text, halt: block("plan-questions", asked), sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "clarify")
        p = spec["planning"]
        assert p["questions"][0]["id"] == "q1" and p["questions_rev"] == 1 and p["spent"]["calls"] == 1
        assert web.post(f"/api/plans/{sid}/answers", json={"revision": 0, "answers": [
            {"id": "q1", "choice": "Narrow"}]}).status_code == 409, "다른 판의 질문에 한 답"
        assert web.post(f"/api/plans/{sid}/answers", json={"revision": 1, "answers": []}).status_code == 400
        assert specs.load("proj", sid)["planning"]["phase"] == "clarify", "답이 오기 전에는 아무것도 보내지 않는다"
        assert web.post(f"/api/plans/{sid}/answers", json={"revision": 1, "answers": [
            {"id": "q1", "choice": "Narrow"}]}).status_code == 200
        settled(sid, "handoff")
    assert "- Which scope? → Narrow" in Host.made[-1].heard[0]
    assert "plan-questions" not in Host.made[-1].heard[0].split("## This phase")[-1], "질문은 한 번만"


# -- failures keep what was done and write nothing ---------------------------------------


def test_an_answer_it_cannot_read_gets_one_retry_then_stops(checkout):
    remote = GitHub()
    Host.replies = [sources, "no blocks here", lambda text, halt: file_block("../escape.md", "x", [], [])]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
    p = spec["planning"]
    assert (p["stopped"]["reason"], p["stopped"]["phase"]) == ("format", "outline")
    assert p["source_manifest"][0]["id"] == "S1", "끝난 조사는 남는다"
    assert "Your answer could not be read" in Host.made[0].heard[2]
    assert not (Path(spec["worktree"]) / "docs").exists() and not (Path(spec["worktree"]).parent / "escape.md").exists()
    assert remote.creates() == 0 and remote.pushes() == 0


@pytest.mark.parametrize("said", ["Looked locally.\n\n" + block("plan-sources", [SOURCE]),
                                  "This host has no web tools, so there are no sources."])
def test_sources_without_a_web_search_behind_them_stop_visibly(checkout, said):
    Host.replies = [lambda text, halt: (said, False, USAGE)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
    assert spec["planning"]["stopped"]["reason"] == "web_unavailable" and len(Host.made[0].heard) == 1, \
        "웹 도구가 없다고 답한 호스트도 형식 오류가 아니라 웹 없음으로 멈춘다"
    assert spec["planning"]["source_manifest"] is None, "로컬 조회를 웹 조사로 적지 않는다"


def test_an_unknown_source_fails_the_checks_and_one_repair_then_stops(checkout):
    remote = GitHub()
    Host.replies = [sources, outline, stage(1, "S9"), stage(2), stage(1, "S9")]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
    p = spec["planning"]
    assert p["stopped"]["reason"] == "invalid" and "`S9`" in p["stopped"]["detail"]
    assert p["repaired"] and p["spent"]["calls"] == 5, "고치는 턴은 한 번뿐"
    assert "S9" in Host.made[0].heard[-1] and "Phase: repair." in Host.made[0].heard[-1]
    assert not (Path(spec["worktree"]) / "docs").exists() and remote.creates() == 0


def test_a_spent_allowance_stops_before_the_next_request_and_keeps_the_drafts(checkout):
    Host.replies = [sources, outline, stage(1)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request(limits={"seconds": 600, "calls": 2, "tokens": 1000})).json()["id"]
        spec = settled(sid, "stopped")
    p = spec["planning"]
    assert (p["stopped"]["reason"], p["stopped"]["phase"]) == ("calls", "stages")
    assert len(Host.made[0].heard) == 2 and [e["path"] for e in p["artifact_manifest"]] == [
        "docs/plans/planner-x/0-overview.md"]


def test_missing_usage_is_not_zero_spent(checkout):
    Host.replies = [lambda text, halt: (sources(text, halt), True, None), outline]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
    p = spec["planning"]
    assert p["stopped"]["reason"] == "budget_unknown" and p["spent"]["unknown"] and len(Host.made[0].heard) == 1


def test_a_failed_turn_without_usage_is_not_zero_and_a_person_may_go_on(checkout):
    def lost(text, halt):
        raise ConnectionError("the host went away mid-turn")

    Host.replies = [sources, lost, outline, stage(1), stage(2)]
    web = client()
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
        p = spec["planning"]
        assert p["stopped"]["reason"] == "host" and p["spent"]["unknown"] and p["spent"]["calls"] == 2
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff")
    assert spec["planning"]["spent"]["unknown"], "재개한 뒤에도 쓴 양은 하한으로 남는다"


def test_the_wall_deadline_cuts_the_turn(checkout):
    Host.replies = [lambda text, halt: (halt.wait(10), "late")[1]]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request(limits={"seconds": 0.5, "calls": 5, "tokens": 1000})).json()["id"]
        spec = settled(sid, "stopped")
    assert spec["planning"]["stopped"]["reason"] == "deadline"


# -- publication, restart and cancellation -------------------------------------------------


def test_a_publication_timeout_recovers_the_pr_it_made_instead_of_making_another(checkout):
    remote = GitHub()
    remote.create_times_out = True
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "handoff")
    assert spec["pr"]["number"] == 7 and remote.creates() == 1


def test_a_restart_stops_the_run_and_a_resume_never_publishes_twice(checkout):
    web = client()
    remote = GitHub()
    remote.push_fails = True
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
        p = spec["planning"]
        assert p["stopped"]["reason"] == "publish_failed" and p["publication"]["head"] and remote.creates() == 0
        # The server went down while it published: a running phase on disk, then start-up.
        planning.planned("proj", sid, phase="publish", stopped=None)
        planning.recover()
        assert specs.load("proj", sid)["planning"]["stopped"]["reason"] == "restart", "스스로 다시 올리지 않는다"

        remote.push_fails = False
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff")
        path = Path(spec["worktree"])
        assert remote.creates() == 1 and git(path, "rev-list", "--count", f"{p['base_head']}..HEAD") == "1", \
            "커밋도 PR 도 한 번"
        assert web.post(f"/api/plans/{sid}/resume").status_code == 409, "멈춘 계획만 잇는다"

        # Cut right after GitHub made it, before it was recorded: found again, not made again.
        planning.planned("proj", sid, phase="stopped", stopped={"reason": "restart", "detail": "", "phase": "publish"},
                         publication={**spec["planning"]["publication"], "pr": None}, handed=None)
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        settled(sid, "handoff")
    assert remote.creates() == 1


def test_a_resumed_publication_pushes_only_its_own_commit(checkout):
    web = client()
    remote = GitHub()
    remote.push_fails = True
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
        path, recorded = Path(spec["worktree"]), spec["planning"]["publication"]["head"]
        # The worktree was let go: someone else's commit lands on top of the recorded one.
        (path / "a.txt").write_text("code\n")
        git(path, "commit", "-qam", "code outside the plan")
        remote.push_fails, pushes = False, remote.pushes()
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "stopped")
    assert spec["planning"]["stopped"]["reason"] == "worktree_moved" and recorded[:7] in spec["planning"]["stopped"]["detail"]
    assert remote.pushes() == pushes and remote.creates() == 0, "기록한 커밋이 아니면 올리지 않는다"


def test_a_commit_cut_before_it_was_recorded_is_resumed(checkout):
    web = client()
    remote = GitHub()
    remote.push_fails = True
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
        # Committed, then the server went down before `publication.head` was saved.
        planning.planned("proj", sid, publication={"head": None, "pr": None})
        remote.push_fails = False
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff")
    path = Path(spec["worktree"])
    assert remote.creates() == 1 and git(path, "rev-list", "--count", f"{spec['planning']['base_head']}..HEAD") == "1"


@pytest.mark.parametrize("left", ["nothing", "a killed swap", "a person's document", "a person's other file"])
def test_a_write_cut_halfway_is_finished_on_resume_only_if_every_file_is_ours(checkout, left):
    web = client()
    remote = GitHub()
    real, writes = planning.atomic, []

    def flaky(file, content):
        if "proj-worktrees" in str(file):
            writes.append(file)
            if len(writes) == 2:
                raise OSError("disk went away")
        real(file, content)

    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote), \
            patch.object(planning, "atomic", flaky):
        sid = web.post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "stopped")
    path, root = Path(spec["worktree"]), spec["artifact_root"]
    folder = path / root
    assert spec["planning"]["stopped"]["reason"] == "publish_failed"
    assert sorted(f.name for f in folder.iterdir()) == ["0-overview.md"], "반만 쓴 폴더, 임시 파일 없이"
    if left == "a killed swap":   # the process died between the write and the swap
        (folder / f".1-entry.md.{'a' * 32}.tmp").write_text("# Sta")
    elif left == "a person's document":
        (folder / "1-entry.md").write_text("# Mine\n\nA person's notes.\n", encoding="utf-8")
    elif left == "a person's other file":
        (folder / "notes.md").write_text("mine\n", encoding="utf-8")
    with patch.object(specs, "sh", remote):
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff", "stopped")
    if left.startswith("a person"):
        assert spec["planning"]["stopped"]["reason"] == "worktree_moved" and remote.creates() == 0
        mine = folder / ("1-entry.md" if left == "a person's document" else "notes.md")
        assert "mine" in mine.read_text(encoding="utf-8").lower(), "사람이 쓴 파일은 덮어쓰지 않는다"
        return
    assert planned_folder(spec) == {f"{root}/0-overview.md", f"{root}/1-entry.md", f"{root}/2-publish.md"}
    assert remote.creates() == 1 and not git(path, "status", "--porcelain", "--ignored")


def test_a_hand_off_cut_by_a_restart_is_resumed(checkout):
    web = client()
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = web.post("/api/plans", json=request()).json()["id"]
        settled(sid, "handoff")
        kicked = len(KICKED)
        # Published, and the server went down before the hand-off.
        planning.planned("proj", sid, handed=None)
        planning.recover()
        p = specs.load("proj", sid)["planning"]
        assert (p["phase"], p["stopped"]["reason"], p["stopped"]["phase"]) == ("stopped", "restart", "handoff")
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff")
    assert spec["planning"]["handed"] and len(KICKED) == kicked + 1


def test_a_hand_off_is_marked_only_once_the_loop_took_it(checkout):
    web = client()
    Host.replies = [sources, outline, stage(1), stage(2)]
    busy = HTTPException(409, "앞 루프가 아직 멈추는 중이다")
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        with patch.object(loop, "kick", side_effect=busy):
            sid = web.post("/api/plans", json=request()).json()["id"]
            p = settled(sid, "stopped")["planning"]
        assert (p["stopped"]["reason"], p["stopped"]["phase"]) == ("broken", "handoff") and not p.get("handed")
        kicked = len(KICKED)
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        assert settled(sid, "handoff")["planning"]["handed"] and len(KICKED) == kicked + 1

        # The loop took it — `kick` moved the state — and the server went down before the mark.
        planning.planned("proj", sid, handed=None)
        specs.update("proj", sid, state="리뷰 대기")
        planning.recover()
        p = specs.load("proj", sid)["planning"]
    assert p["phase"] == "handoff" and p["handed"] and len(KICKED) == kicked + 1, "두 번 넘기지 않는다"


def test_a_restart_mid_turn_keeps_the_call_and_marks_its_spend_unknown(checkout):
    seen = []

    def watched(text, halt):
        seen.append(specs.load("proj", "planner-x"))   # what a restart right now would find on disk
        return sources(text, halt)

    Host.replies = [watched, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request()).json()["id"]
        settled(sid, "handoff")
    cut = seen[0]
    assert cut["planning"]["inflight"] and cut["planning"]["spent"]["calls"] == 1
    with specs._files:
        specs.save(cut)
    planning.recover()
    p = specs.load("proj", sid)["planning"]
    assert (p["phase"], p["stopped"]["reason"]) == ("stopped", "restart")
    assert p["spent"]["calls"] == 1 and p["spent"]["unknown"] and not p["inflight"]


def test_a_lost_transcript_row_keeps_the_turn_s_usage(checkout):
    real, lost = work.remember, []

    def flaky(path, role, text, **extra):
        if role == "assistant" and not lost:
            lost.append(text)
            raise PermissionError("the record is held open")
        real(path, role, text, **extra)

    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()), \
            patch.object(work, "remember", flaky):
        sid = client().post("/api/plans", json=request()).json()["id"]
        p = settled(sid, "handoff")["planning"]
    assert lost and (p["spent"]["calls"], p["spent"]["tokens"], p["spent"]["unknown"], p["inflight"]) == (4, 60, False, False)


def test_a_turn_that_breaks_after_it_was_sent_is_unknown_spend(checkout):
    real = planning.consume

    def breaks(path, run, text):
        if "Phase: outline" in text:
            raise RuntimeError("broke after the send")
        return real(path, run, text)

    Host.replies = [sources, outline]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()), \
            patch.object(planning, "consume", breaks):
        sid = client().post("/api/plans", json=request()).json()["id"]
        p = settled(sid, "stopped")["planning"]
    assert p["stopped"]["reason"] == "broken" and p["spent"]["calls"] == 2
    assert p["spent"]["unknown"] and not p["inflight"], "기록 전에 깨진 턴은 0 이 아니라 모름이다"


def test_a_crlf_document_keeps_its_own_hash(checkout):
    def crlf(n):
        make = stage(n)
        return lambda text, halt: make(text, halt).replace("\\n", "\\r\\n")   # inside the JSON string

    Host.replies = [sources, outline, crlf(1), crlf(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "handoff")
    p = spec["planning"]
    assert not p.get("repaired") and p["spent"]["calls"] == 4, "고치는 턴 없이 지난다"
    path = Path(spec["worktree"])
    for e in p["artifact_manifest"]:
        data = (path / e["path"]).read_bytes()
        assert b"\r\n" not in data and planning.sha(data.decode("utf-8")) == e["sha256"]


def test_cancel_keeps_the_research_and_resume_goes_on_from_there(checkout):
    web = client()
    entered = threading.Event()

    def slow_outline(text, halt):
        entered.set()
        halt.wait(10)   # the cancel reaches the turn through its stop
        return outline(text, halt)

    Host.replies = [sources, slow_outline, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", GitHub()):
        sid = web.post("/api/plans", json=request()).json()["id"]
        assert entered.wait(10)
        assert web.post(f"/api/plans/{sid}/cancel").status_code == 200
        spec = settled(sid, "stopped")
        assert spec["planning"]["stopped"]["reason"] == "cancelled" and spec["planning"]["source_manifest"]
        assert web.post(f"/api/plans/{sid}/resume").status_code == 200
        spec = settled(sid, "handoff")
    assert spec["state"] == "PR #7"


# -- the boundary -------------------------------------------------------------------------------


def test_only_a_plan_file_directly_in_the_new_folder_passes(tmp_path):
    root = "docs/plans/p"
    assert planning.target(tmp_path, root, "docs/plans/p/1-a.md") == (tmp_path / root / "1-a.md").resolve()
    for bad in ("../1-a.md", "docs/plans/p/../q/1-a.md", "/etc/1-a.md", "C:/x/1-a.md", "docs\\plans\\p\\1-a.md",
                "docs/plans/p/sub/1-a.md", "docs/plans/p/1-a.txt", "docs/plans/p/README.md", "docs/plans/q/1-a.md",
                None):
        with pytest.raises(ValueError):
            planning.target(tmp_path, root, bad)
    (tmp_path / "docs/plans").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = tmp_path / "docs/plans/p"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        try:
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        except (ImportError, OSError):
            pytest.skip("링크를 만들 수 없는 환경")
    with pytest.raises(ValueError, match="링크"):
        planning.target(tmp_path, root, "docs/plans/p/1-a.md")


def test_the_reviser_writes_only_inside_the_plan_and_commits_exactly_that(checkout):
    remote = GitHub()
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "handoff")
    path, root = Path(spec["worktree"]), spec["artifact_root"]
    head = git(path, "rev-parse", "HEAD")
    lp = SimpleNamespace(halt=threading.Event(), run=None)
    fixed = stage_md("R1").replace("Proposed.", "Proposed, now with the entry route.")
    outside = file_block("a.txt", "overwritten\n", [], [])
    disposition = "\n\n```disposition\n[]\n```"
    Host.replies = [file_block(f"{root}/1-entry.md", fixed, ["R1"], ["S1"]) + "\n\n" + outside + disposition,
                    file_block(f"{root}/1-entry.md", fixed, ["R1"], ["S1"]) + disposition]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        assert "disposition" in planning.revise(lp, spec, path, "Fix F1.")
        assert git(path, "rev-parse", "HEAD") == head and (path / "a.txt").read_text() == "a\n", \
            "밖을 가리키는 파일이 하나라도 있으면 아무것도 쓰지 않는다"
        planning.revise(lp, spec, path, "Fix F1.")
    reviser = Host.made[-1]
    assert reviser.tools == planning.REVISER_TOOLS and reviser.closed and reviser.model == "codex:gpt-6.1-sol"
    assert git(path, "diff", "--name-only", head, "HEAD") == f"{root}/1-entry.md"
    assert "now with the entry route" in (path / root / "1-entry.md").read_text(encoding="utf-8")
    assert not git(path, "status", "--porcelain") and str(path) not in work._busy


def test_a_reviser_turn_stopped_on_its_own_writes_nothing(checkout):
    remote = GitHub()
    Host.replies = [sources, outline, stage(1), stage(2)]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        sid = client().post("/api/plans", json=request()).json()["id"]
        spec = settled(sid, "handoff")
    path, root = Path(spec["worktree"]), spec["artifact_root"]
    head, before = git(path, "rev-parse", "HEAD"), (path / root / "1-entry.md").read_bytes()
    lp = SimpleNamespace(halt=threading.Event(), run=None)
    fixed = file_block(f"{root}/1-entry.md", stage_md("R1").replace("Proposed.", "Changed."), ["R1"], ["S1"])
    # `/api/work/stop` sets the turn's own stop, not the loop's.
    Host.replies = [lambda text, halt: (halt.set(), fixed)[1]]
    with patch.object(planning, "ChatSession", Host), patch.object(specs, "sh", remote):
        assert planning.revise(lp, spec, path, "Fix F1.") == ""
    assert git(path, "rev-parse", "HEAD") == head and (path / root / "1-entry.md").read_bytes() == before


def test_a_pr_that_is_already_open_is_used_not_made_again(checkout):
    remote = GitHub()
    remote.prs.append({"number": 9, "url": "https://github.com/o/proj/pull/9", "headRefName": "b",
                       "baseRefName": "main", "isCrossRepository": False})
    with patch.object(specs, "sh", remote):
        assert specs.pull_request(checkout, "b", "main", "t", "body") == (9, "https://github.com/o/proj/pull/9")
        remote.prs[0]["isCrossRepository"] = True
        assert specs.pull_request(checkout, "b", "main", "t", "body")[0] == 7, "포크의 같은 이름 브랜치는 남의 것이다"
    assert remote.creates() == 1
