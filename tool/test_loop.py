"""The review loop: rounds between a read-only review cell and the work cell,
the stops and what `[계속]` does for each, the pull request list and `adopt`,
and `[머지]` through the cleanup.

Both cells are stand-ins. GitHub is a stand-in behind `specs.sh`; git is real,
against a bare origin, so pushes, the squash merge and the lease on deleting
the remote branch all run through git itself.
"""

import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import chat_session
from agent.chat_session import Event
from main import channels as chat_channels
from main import loop, specs, work
from main import query as chat
from test_main import _repo, client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_specs import Worker
from workspace import adopt, create

GATE = "python gate.py"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def commit(path: Path, name: str, text: str = "x\n") -> str:
    (path / name).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", name)
    return git(path, "rev-parse", "HEAD")


def waited(test, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.05)
    raise AssertionError("기다린 일이 오지 않았다")


class Hub:
    """GitHub as `specs.sh` sees it. Every `git` runs for real; a pull
    request's head is its branch in the bare origin, and a merge is a real
    squash onto the base there."""

    def __init__(self, origin: Path, scratch: Path):
        self.origin, self.scratch, self.real = origin, scratch, specs.sh
        self.prs: dict[int, dict] = {}
        self.calls: list[list[str]] = []
        self.comments: list[tuple[int, str]] = []
        self.merge_as = "merged"   # merged · queued · auto · open
        self.broken = False        # every `gh` fails, as when the network is down

    def open(self, n: int, branch: str, **extra) -> None:
        self.prs[n] = {"branch": branch, "base": "main", "state": "OPEN", "title": f"PR {n}", "body": "",
                       "cross": False, "auto": None, "queue": False, "merged": None, **extra}

    def head(self, n: int) -> str:
        return git(self.origin, "rev-parse", f"refs/heads/{self.prs[n]['branch']}")

    def elsewhere(self) -> Path:
        """Another clone of the origin: someone else's machine, or GitHub's."""

        if not self.scratch.exists():
            subprocess.run(["git", "clone", "-q", str(self.origin), str(self.scratch)], check=True)
            git(self.scratch, "config", "user.email", "o@o")
            git(self.scratch, "config", "user.name", "o")
        git(self.scratch, "fetch", "-q", "origin")
        return self.scratch

    def push_elsewhere(self, n: int, name: str = "") -> str:
        """Someone else pushes to the pull request's branch."""

        branch = self.prs[n]["branch"]
        git(self.elsewhere(), "checkout", "-q", "-B", branch, f"origin/{branch}")
        sha = commit(self.scratch, name or f"other-{uuid.uuid4().hex[:6]}.txt")
        git(self.scratch, "push", "-q", "origin", branch)
        return sha

    def squash(self, n: int) -> str:
        pr = self.prs[n]
        self.elsewhere()
        git(self.scratch, "checkout", "-q", "-B", pr["base"], f"origin/{pr['base']}")
        git(self.scratch, "merge", "-q", "--squash", f"origin/{pr['branch']}")
        git(self.scratch, "commit", "-qm", f"squash #{n}")
        git(self.scratch, "push", "-q", "origin", pr["base"])
        return git(self.scratch, "rev-parse", "HEAD")

    def full(self, n: int) -> dict:
        pr = self.prs[n]
        return {"number": n, "title": pr["title"], "body": pr["body"], "headRefName": pr["branch"],
                "headRefOid": self.head(n), "baseRefName": pr["base"], "state": pr["state"],
                "mergeCommit": {"oid": pr["merged"]} if pr["merged"] else None, "autoMergeRequest": pr["auto"],
                "isCrossRepository": pr["cross"], "headRepositoryOwner": {"login": "o"},
                "url": f"https://github.com/o/proj/pull/{n}", "mergedAt": None}

    def __call__(self, args, cwd, timeout=60):
        self.calls.append(args)
        if args[0] != "gh":
            return self.real(args, cwd, timeout)
        ok = lambda out="": subprocess.CompletedProcess(args, 0, out, "")  # noqa: E731
        no = lambda err: subprocess.CompletedProcess(args, 1, "", err + "\n")  # noqa: E731
        if self.broken:
            return no("error connecting to api.github.com")
        what = args[1:3]
        if what == ["pr", "view"]:
            fields = args[args.index("--json") + 1].split(",")
            full = self.full(int(args[3]))
            return ok(json.dumps({f: full[f] for f in fields if f in full}))
        if what == ["pr", "list"]:
            return ok(json.dumps([self.full(n) for n, pr in self.prs.items() if pr["state"] == "OPEN"]))
        if what == ["pr", "merge"]:
            n = int(args[3])
            if args[args.index("--match-head-commit") + 1] != self.head(n):
                return no("GraphQL: Head branch was modified. Review and try the merge again.")
            if self.merge_as == "merged":
                self.prs[n].update(state="MERGED", merged=self.squash(n))
            elif self.merge_as == "queued":
                self.prs[n]["queue"] = True
            elif self.merge_as == "auto":
                self.prs[n]["auto"] = {"enabledAt": "now"}
            return ok()
        if what == ["pr", "comment"]:
            self.comments.append((int(args[3]), Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")))
            return ok()
        if what == ["pr", "diff"]:
            return ok(f"diff --git a/x b/x\n+the diff of #{args[3]}\n")
        if what == ["api", "graphql"]:
            n = int(next(a for a in args if a.startswith("n="))[2:])
            return ok(json.dumps({"data": {"repository": {"pullRequest": {"isInMergeQueue": self.prs[n]["queue"]}}}}))
        if what == ["repo", "view"]:
            return ok("main\n")
        return no(f"unknown: {' '.join(args)}")


class Reviewer:
    """A review cell that answers each turn with the next of `replies`. A
    callable reply gets the first line of the round's instruction — what the
    answer's own first line must say — and the turn's text."""

    replies: list = []
    made: list = []

    def __init__(self, path, tools="", system="", model="", effort="", **_):
        self.path, self.tools, self.model, self.effort = Path(path), tools, model, effort
        self.is_codex, self.session_id, self.alive, self.heard = model.startswith("codex:"), None, True, []
        Reviewer.made.append(self)

    def say(self, text, halt=None):
        self.heard.append(text)
        named = re.search(r"`([^`]+round-\d+\.md)`", text)
        first = Path(named[1]).read_text(encoding="utf-8").splitlines()[0] if named else ""
        reply = Reviewer.replies.pop(0) if Reviewer.replies else allow
        if callable(reply):
            reply = reply(first, text)
        yield Event("done", reply, {"session_id": f"review-{id(self)}", "error": False})

    def stop(self, halt):
        pass

    def close(self):
        self.alive = False


def allow(first, _text=""):
    return f"{first}\n새 발견 없음\n머지 허용"


def deny(*findings):
    return lambda first, _text: f"{first}\n" + "\n".join(findings) + "\n머지 불가 — 고칠 것이 있다"


def keep(*kept):
    return "고른 것.\n\n```p2-keep\n" + json.dumps(list(kept), ensure_ascii=False) + "\n```"


def fixed(*entries, name=None, broken=False):
    """A work cell's reply: a commit, and a disposition of `(finding, action)`."""

    def reply(path, halt):
        commit(path, name or f"fix-{uuid.uuid4().hex[:6]}.txt")
        if broken:
            commit(path, "broken")
        items = [{"finding": f, "action": a, "evidence": "ran it"} for f, a in entries]
        return "고쳤다.\n\n```disposition\n" + json.dumps(items, ensure_ascii=False) + "\n```"
    return reply


def unbroken(path, halt):
    (path / "broken").unlink()
    git(path, "commit", "-qam", "unbreak")
    return "게이트를 고쳤다"


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = _repo(tmp_path)
    (repo / "gate.py").write_text("import os, sys\nsys.exit(1 if os.path.exists('broken') else 0)\n", encoding="utf-8")
    (repo / ".wiki").mkdir()
    (repo / ".wiki/adapter.toml").write_text(f'[slots]\ngate_cmd = "{GATE}"\n', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "gate")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    hub = Hub(origin, tmp_path / "elsewhere")
    Reviewer.replies, Reviewer.made, Worker.replies, Worker.made = [], [], [], []
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Worker), \
         patch.object(loop, "ChatSession", Reviewer), patch.object(loop, "review_model", lambda: "codex:test"), \
         patch.object(specs, "sh", hub), patch.object(loop, "REVIEW", tmp_path / "review"), \
         patch.object(loop, "_cells", {}), patch.object(loop, "_loops", {}), patch.object(loop, "_seated", 0):
        yield SimpleNamespace(repo=repo, hub=hub, origin=origin, tmp=tmp_path)
        for running in list(loop._loops.values()):
            running.stop()
            running.thread.join(10)
        work.close_all()


def pr_spec(w, name: str, n: int, **extra) -> dict:
    """A spec as stage 3 leaves it: the pull request up, its gate passed."""

    path = create(w.repo, name)
    head = commit(path, f"{name}.txt")
    git(path, "push", "-q", "-u", "origin", name)
    w.hub.open(n, name)
    spec = {"id": name, "repo": "proj", "rev": 1, "goal": f"{name} 의 목표", "out": [], "done": [GATE],
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [],
            "source": {"focus": "next", "turn": 1.0, "plan": None}, "state": f"PR #{n}", "stopped": None,
            "worktree": str(path), "pr": {"number": n, "url": f"https://github.com/o/proj/pull/{n}",
                                          "base": "main", "head": head, "branch": name},
            "report": None, "gate": {"ok": True, "reason": "", "cmd": GATE, "tail": "", "head": head},
            "fault": None, "rounds": [], "history": [{"ts": time.time(), "state": f"PR #{n}"}], **extra}
    specs.save(spec)
    return spec


def looped(name: str) -> dict:
    """Start the spec's loop and wait until it has stopped driving."""

    loop.kick("proj", name)
    waited(lambda: ("proj", name) not in loop._loops)
    return specs.load("proj", name)


def order(w, n: int, r: int, what: str = "") -> str:
    return (w.tmp / "review" / "proj" / str(n) / f"round-{r}{what}.md").read_text(encoding="utf-8")


# -- the round's result ---------------------------------------------------------------


def test_the_parser_checks_the_round_the_head_and_the_last_line():
    head = "abcdef0123456789abcdef0123456789abcdef01"
    good = ("# Round 3 · PR #12 · abcdef0\n\n[P1] tool/x.py:14 — 틀렸다\n재현: 이렇게\n"
            "[P2] tool/y.py:2 — 이름\n\n**머지 불가 — P1 하나**")
    parsed = loop.parse(good, 3, 12, head)
    assert parsed["verdict"] == "deny" and parsed["counts"] == {"P0": 0, "P1": 1, "P2": 1}
    assert parsed["findings"][0]["file"] == "tool/x.py" and parsed["findings"][0]["body"] == "재현: 이렇게"
    assert loop.parse("Round 1 · PR #12 · abcdef0\n새 발견 없음\n머지 허용", 1, 12, head)["verdict"] == "allow"
    for text, why in ((good.replace("Round 3", "Round 2"), "라운드 번호"),
                      (good.replace("abcdef0", "1234567"), "머리 커밋"),
                      (good.replace("PR #12", "PR #13"), "PR 번호"),
                      (good.rsplit("\n", 1)[0], "마지막 줄"),
                      ("여기 결과다\n" + good, "첫 줄"),
                      (good.replace("머지 불가", "머지해도 된다"), "마지막 줄")):
        with pytest.raises(ValueError, match=why):
            loop.parse(text, 3, 12, head)


def test_the_stop_reasons_are_the_plan_s_table_and_nothing_else():
    plan = (Path(__file__).resolve().parents[1] / "docs/plans/loop/4-review.md").read_text(encoding="utf-8")
    table = plan.split("| 멈춤 이유 | 다시 시작 |")[1].split("\n\n")[0]
    listed = re.findall(r"^\| `([^`]+)` \|", table, re.M)
    assert listed == [w.value for w in loop.Why]
    with pytest.raises(ValueError, match="표에 없는"):
        loop.stop(None, "proj", "x", "승인 대기")


# -- rounds --------------------------------------------------------------------------------


def test_an_allow_ends_at_mergeable_and_keeps_the_p2_worth_doing(world):
    spec = pr_spec(world, "fix-a", 7)
    Reviewer.replies = [lambda first, _: f"{first}\n[P2] a.txt:1 — 이름이 모호하다\n머지 허용",
                        keep("a.txt:1 — 이름을 바꾼다")]
    spec = looped("fix-a")
    assert spec["state"] == "머지 가능" and spec["rounds"][0]["verdict"] == "allow"
    assert spec["rounds"][0]["findings"] == {"P0": 0, "P1": 0, "P2": 1}
    assert spec["p2"] == ["a.txt:1 — 이름을 바꾼다"] and "- a.txt:1 — 이름을 바꾼다" in spec["p2_comment"]
    first = order(world, 7, 1)
    assert first.startswith(f"Round 1 · PR #7 · {spec['pr']['head'][:7]}")
    assert "(first round)" in first and "1 file changed" in first and "passed" in first
    assert order(world, 7, 1, "-result").endswith("머지 허용")
    assert Worker.made == [], "허용이면 작업 셀에 보내지 않는다"
    assert specs.view(world.repo, spec)["approved"] == spec["rounds"][0]["head"]


def test_a_refusal_goes_to_the_work_cell_through_the_gate_and_up(world):
    """The gate fails once after the fix and is sent back with its tail; the
    second run passes, the fix is pushed, and the next round names it all."""

    pr_spec(world, "fix-b", 7)
    finding = "[P1] a.txt:1 — 값이 틀렸다"
    Reviewer.replies = [deny(finding, "[P2] b.txt:2 — 이름"), allow, keep()]
    Worker.replies = [fixed((finding, "fixed"), broken=True), unbroken]
    spec = looped("fix-b")
    assert spec["state"] == "머지 가능" and [r["verdict"] for r in spec["rounds"]] == ["deny", "allow"]
    heard = Worker.made[-1].heard
    assert "[P1] a.txt:1" in heard[0] and "b.txt:2" not in heard[0], "P2 는 작업 셀에 보내지 않는다"
    assert "python gate.py" in heard[1] and "failed" in heard[1]
    assert world.hub.head(7) == git(Path(spec["worktree"]), "rev-parse", "HEAD"), "고친 것이 올라갔다"
    second = order(world, 7, 2)
    assert '"action": "fixed"' in second and "Since round 1" in second
    assert "- [P2] b.txt:2 — 이름" in second.split("## Deferred P2")[1]
    assert spec["rounds"][0]["disposition"][0]["finding"] == finding


def test_the_gate_failing_twice_in_a_row_stops(world):
    pr_spec(world, "fix-c", 7)
    Reviewer.replies = [deny("[P1] a.txt:1 — 틀렸다")]
    Worker.replies = [fixed(("[P1] a.txt:1 — 틀렸다", "fixed"), broken=True), "못 고쳤다"]
    spec = looped("fix-c")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == "게이트"
    assert world.hub.head(7) != git(Path(spec["worktree"]), "rev-parse", "HEAD"), "통과하지 못한 것은 올리지 않는다"


def test_the_round_cap_stops_and_continue_adds_four_to_this_spec(world):
    loop.store(rounds=2)
    pr_spec(world, "fix-d", 7)
    Reviewer.replies = [deny("[P1] a.txt:1 — 하나"), deny("[P1] a.txt:5 — 둘")]
    Worker.replies = [fixed(("[P1] a.txt:1 — 하나", "fixed")), fixed(("[P1] a.txt:5 — 둘", "fixed"))]
    spec = looped("fix-d")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == "라운드 상한" and len(spec["rounds"]) == 2
    Reviewer.replies = [allow]
    assert client().post("/api/specs/fix-d/resume", json={}).status_code == 200
    waited(lambda: ("proj", "fix-d") not in loop._loops)
    spec = specs.load("proj", "fix-d")
    assert spec["extra"] == 4 and spec["state"] == "머지 가능" and loop.cap(spec) == 6


def test_the_same_finding_disputed_twice_stops_for_a_person(world):
    pr_spec(world, "fix-e", 7)
    finding = "[P1] a.txt:1 — 경계가 틀렸다"
    Reviewer.replies = [deny(finding), deny("[P1] a.txt:1 — 경계가 틀렸다 (다시)")]
    Worker.replies = [fixed((finding, "disagree")), fixed(("[P1] a.txt:1 — 다시", "disagree"))]
    spec = looped("fix-e")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == "반론" and len(spec["rounds"]) == 2
    web = client()
    assert web.post("/api/specs/fix-e/resume", json={}).status_code == 400
    Reviewer.replies = [allow]
    web.post("/api/specs/fix-e/resume", json={"note": "경계는 지금이 맞다 — 반열린 구간"}).raise_for_status()
    waited(lambda: ("proj", "fix-e") not in loop._loops)
    assert specs.load("proj", "fix-e")["state"] == "머지 가능"
    assert "경계는 지금이 맞다" in order(world, 7, 3)


def test_findings_that_do_not_shrink_bring_the_grouping_section(world):
    spec = pr_spec(world, "fix-f", 7)
    path, head = Path(spec["worktree"]), spec["pr"]["head"]

    def rounds(*counts):
        return [{"n": i + 1, "head": head, "base": "main", "verdict": "deny", "disposition": [],
                 "findings": {"P0": 0, "P1": c, "P2": 0}} for i, c in enumerate(counts)]

    assert "Sort the findings by family" in loop.instruction({**spec, "rounds": rounds(2, 2, 3)}, path, 4, head,
                                                              "main", True)
    assert "Sort the findings" not in loop.instruction({**spec, "rounds": rounds(3, 2, 2)}, path, 4, head, "main", True)
    assert "## `gh pr diff" not in loop.instruction(spec, path, 1, head, "main", True)
    claude = loop.instruction(spec, path, 1, head, "main", False)
    assert "## `gh pr diff 7`" in claude and "+the diff of #7" in claude


def test_four_loops_with_three_seats_leave_the_fourth_waiting(world):
    let_go = threading.Event()

    def held(first, _text):
        let_go.wait(20)
        return allow(first)

    for i in range(4):
        pr_spec(world, f"seat-{i}", 10 + i)
    Reviewer.replies = [held] * 4
    for i in range(4):
        loop.kick("proj", f"seat-{i}")
    waited(lambda: sorted(specs.load("proj", f"seat-{i}")["state"] for i in range(4))
           == ["리뷰 R1", "리뷰 R1", "리뷰 R1", "리뷰 대기"])
    time.sleep(0.3)
    assert sum(specs.load("proj", f"seat-{i}")["state"] == "리뷰 대기" for i in range(4)) == 1
    let_go.set()
    waited(lambda: not loop._loops)
    assert {specs.load("proj", f"seat-{i}")["state"] for i in range(4)} == {"머지 가능"}


def test_a_head_that_moved_while_it_was_read_throws_the_round_away(world):
    pr_spec(world, "fix-g", 7)

    def pushed_meanwhile(first, _text):
        world.hub.push_elsewhere(7)
        return allow(first)

    Reviewer.replies = [pushed_meanwhile, allow]
    spec = looped("fix-g")
    assert spec["state"] == "머지 가능"
    assert [r.get("stale", False) for r in spec["rounds"]] == [True, False]
    assert spec["rounds"][1]["n"] == 1 and spec["rounds"][1]["head"] == world.hub.head(7)
    assert git(Path(spec["worktree"]), "rev-parse", "HEAD") == world.hub.head(7), "작업트리도 새 머리로"
    assert (world.tmp / "review/proj/7" / f"round-1-stale-{spec['rounds'][0]['head'][:7]}-result.md").exists()


def test_a_base_that_moved_throws_the_round_away_and_merge_refuses_it(world):
    pr_spec(world, "fix-h", 7)

    def rebased(first, _text):
        world.hub.prs[7]["base"] = "dev"
        return allow(first)

    Reviewer.replies = [rebased, allow]
    spec = looped("fix-h")
    assert [r.get("stale", False) for r in spec["rounds"]] == [True, False] and spec["rounds"][1]["base"] == "dev"
    world.hub.prs[7]["base"] = "main"
    answer = client().post("/api/specs/fix-h/merge", json={"head": spec["rounds"][1]["head"]})
    assert answer.status_code == 409 and "base" in answer.json()["detail"]
    assert not any(c[1:3] == ["pr", "merge"] for c in world.hub.calls)
    waited(lambda: ("proj", "fix-h") not in loop._loops)


def test_a_stop_that_loses_the_race_to_the_loop_writes_nothing(world):
    """`[멈춤]` read a running state, and the loop reached `머지 가능` before
    the stop was written. The stop must not write over it."""

    pr_spec(world, "fix-t", 7)
    specs.update("proj", "fix-t", state="리뷰 R1")

    class Finishing:
        def stop(self):
            specs.update("proj", "fix-t", state="머지 가능")

    loop._loops[("proj", "fix-t")] = Finishing()
    answer = client().post("/api/specs/fix-t/halt")
    del loop._loops[("proj", "fix-t")]
    assert answer.status_code == 409
    assert specs.load("proj", "fix-t")["state"] == "머지 가능" and specs.load("proj", "fix-t")["stopped"] is None
    # The same guard for every writer outside a loop: a stop from a state it
    # was not meant for writes nothing.
    loop.stop(None, "proj", "fix-t", loop.Why.RESTART)
    assert specs.load("proj", "fix-t")["state"] == "머지 가능"


def test_a_restart_stops_every_running_loop(world):
    pr_spec(world, "fix-i", 7)
    specs.update("proj", "fix-i", state="리뷰 R2")
    pr_spec(world, "fix-j", 8)
    specs.update("proj", "fix-j", state="머지 가능")
    loop.recover()
    assert specs.load("proj", "fix-i")["stopped"] == {"reason": "서버 재시작", "detail": ""}
    assert specs.load("proj", "fix-j")["state"] == "머지 가능"


# -- the pull requests ---------------------------------------------------------------------


def test_a_fork_cannot_be_picked_and_a_pr_without_a_spec_gets_a_minimal_one(world):
    for branch in ("feat/login", "from-fork"):
        git(world.repo, "push", "-q", "origin", f"main:refs/heads/{branch}")
    world.hub.open(21, "feat/login", title="로그인 뒤 돌아간다",
                   body="## 변경 요약\n\n무엇\n\n## 변경 이유\n\n- 서버에서 한다 — 화면은 모른다\n\n## 확인\n")
    world.hub.open(22, "from-fork", cross=True)
    world.hub.push_elsewhere(21, "login.txt")
    web = client()
    rows = {r["number"]: r for r in web.get("/api/prs").json()["rows"]}
    assert rows[21]["pickable"] and not rows[22]["pickable"] and rows[22]["why"].startswith("포크")
    results = web.post("/api/loops", json={"prs": [21, 22]}).json()["results"]
    assert results[0] == {"number": 21, "id": "feat-login"} and results[1]["error"].startswith("포크")
    waited(lambda: not loop._loops)
    spec = specs.load("proj", "feat-login")
    assert spec["goal"] == "로그인 뒤 돌아간다" and spec["done"] == [GATE]
    assert spec["decisions"] == [{"what": "서버에서 한다 — 화면은 모른다", "why": "", "rejected": ""}]
    assert spec["state"] == "머지 가능" and git(Path(spec["worktree"]), "rev-parse", "HEAD") == world.hub.head(21)
    assert spec["pr"]["branch"] == "feat/login"
    assert web.get("/api/prs").json()["rows"][0]["pickable"] is False


def test_adopt_takes_the_branch_as_it_is_and_refuses_what_it_would_have_to_move(world):
    repo, hub = world.repo, world.hub
    for branch in ("a1", "a2", "a3", "a4", "a5"):
        hub.open(len(hub.prs) + 30, branch)
        git(repo, "push", "-q", "origin", f"main:refs/heads/{branch}")
    oid = {b: git(world.origin, "rev-parse", f"refs/heads/{b}") for b in ("a1", "a2", "a3", "a4", "a5")}

    # No local branch: a new one tracking the remote.
    path = adopt(repo, "a1", oid["a1"])
    assert git(path, "rev-parse", "HEAD") == oid["a1"] and git(path, "rev-parse", "--abbrev-ref", "@{u}") == "origin/a1"
    # A local branch at the head: used as it is.
    git(repo, "branch", "a2", oid["a2"])
    assert git(adopt(repo, "a2", oid["a2"]), "rev-parse", "HEAD") == oid["a2"]
    # A local branch elsewhere: refused, and neither moved nor removed.
    git(repo, "branch", "a3", "HEAD~1")
    before = git(repo, "rev-parse", "a3")
    with pytest.raises(RuntimeError, match="PR 머리와 다르다"):
        adopt(repo, "a3", oid["a3"])
    assert git(repo, "rev-parse", "a3") == before and not (repo.parent / "proj-worktrees/a3").exists()
    # Checked out in another worktree: git's own reason.
    git(repo, "branch", "a4", oid["a4"])
    git(repo, "worktree", "add", "-q", str(world.tmp / "a4-elsewhere"), "a4")
    with pytest.raises(RuntimeError, match="a4"):
        adopt(repo, "a4", oid["a4"])
    # A new branch that does not land on the pull request's head: taken away.
    with pytest.raises(RuntimeError, match="PR 머리에 서지 않았다"):
        adopt(repo, "a5", git(repo, "rev-parse", "main~1"))
    assert not (repo.parent / "proj-worktrees/a5").exists()
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "refs/heads/a5"],
                          capture_output=True).returncode != 0


# -- merging -------------------------------------------------------------------------------------


def test_merge_is_bound_to_the_allowed_head_and_cleans_up_after(world):
    pr_spec(world, "fix-k", 7)
    Reviewer.replies = [lambda first, _: f"{first}\n[P2] a.txt:1 — 이름\n머지 허용", keep("a.txt:1 — 이름을 바꾼다")]
    spec = looped("fix-k")
    approved, path = spec["rounds"][0]["head"], Path(spec["worktree"])
    done = client().post("/api/specs/fix-k/merge", json={"head": approved})
    assert done.status_code == 200, done.text
    merge = next(c for c in world.hub.calls if c[1:3] == ["pr", "merge"])
    assert merge[merge.index("--match-head-commit") + 1] == approved and "--squash" in merge
    spec = specs.load("proj", "fix-k")
    assert spec["state"] == "머지됨" and spec["merge"]["base"] == "main"
    assert world.hub.comments == [(7, spec["p2_comment"])]
    assert git(world.repo, "rev-parse", "HEAD") == git(world.origin, "rev-parse", "main"), "원본이 앞으로 갔다"
    assert not path.exists() and not git(world.repo, "branch", "--list", "fix-k"), "작업트리와 브랜치를 지웠다"
    assert not git(world.origin, "branch", "--list", "fix-k"), "원격 브랜치도"
    assert loop._cells == {} and (world.tmp / "review/proj/7/round-1.md").exists()
    told = [r["text"] for r in chat.recall("next") if r["role"] == "result"]
    assert told[-1] == "PR #7 머지됨 — 라운드 1, 남은 P2 1"


def test_merge_refuses_a_head_the_review_did_not_allow(world):
    pr_spec(world, "fix-l", 7)
    spec = looped("fix-l")
    newer = commit(Path(spec["worktree"]), "late.txt")
    answer = client().post("/api/specs/fix-l/merge", json={"head": newer})
    assert answer.status_code == 409 and "새 커밋" in answer.json()["detail"]
    assert not any(c[1:3] == ["pr", "merge"] for c in world.hub.calls)
    waited(lambda: ("proj", "fix-l") not in loop._loops)
    spec = specs.load("proj", "fix-l")
    assert spec["state"] == "머지 가능" and len(spec["rounds"]) == 2, "새 라운드를 받았다"


def test_a_merge_that_only_queued_cleans_nothing_until_it_lands(world):
    pr_spec(world, "fix-m", 7)
    spec = looped("fix-m")
    path = Path(spec["worktree"])
    world.hub.merge_as = "queued"
    client().post("/api/specs/fix-m/merge", json={"head": spec["rounds"][0]["head"]}).raise_for_status()
    assert specs.load("proj", "fix-m")["state"] == "머지 대기"
    assert path.exists() and git(world.repo, "branch", "--list", "fix-m") and git(world.origin, "branch", "--list", "fix-m")
    world.hub.prs[7].update(state="MERGED", merged=world.hub.squash(7), queue=False)
    client().get("/api/specs")   # the window comes back into focus
    assert specs.load("proj", "fix-m")["state"] == "머지됨" and not path.exists()


def test_every_row_of_the_after_merge_table(world):
    """One `state` and one condition per row; a read that fails changes nothing."""

    cases = [("MERGED", {}, "머지됨", None), ("MERGED", {"base": "dev"}, "멈춤", "검토하지 않은 base 에 머지됨"),
             ("OPEN", {"queue": True}, "머지 대기", None), ("OPEN", {"auto": {"enabledAt": "x"}}, "머지 대기", None),
             ("OPEN", {}, "멈춤", "머지 대기에서 빠짐"), ("CLOSED", {}, "멈춤", "머지 대기에서 빠짐")]
    for i, (state, extra, after, reason) in enumerate(cases):
        name, n = f"row-{i}", 40 + i
        spec = pr_spec(world, name, n)
        head = spec["pr"]["head"]
        specs.update("proj", name, state="머지 대기", rounds=[{"n": 1, "head": head, "base": "main", "verdict": "allow",
                                                             "findings": {"P0": 0, "P1": 0, "P2": 0}}])
        world.hub.prs[n].update(state=state, **extra)
        loop.landed(world.repo, spec)
        got = specs.load("proj", name)
        assert (got["state"], (got["stopped"] or {}).get("reason")) == (after, reason), (state, extra)
        # Only `머지됨` cleans up; the merge commit may be empty and still is.
        assert Path(spec["worktree"]).exists() == (after != "머지됨"), (state, extra)
        if after == "머지됨":
            assert got["merge"] == {"commit": "", "base": "main"}
    assert world.hub.comments and "dev" in world.hub.comments[-1][1]

    spec = pr_spec(world, "row-broken", 60)
    specs.update("proj", "row-broken", state="머지 대기")
    world.hub.broken = True
    loop.landed(world.repo, spec)
    assert specs.load("proj", "row-broken")["state"] == "머지 대기" and Path(spec["worktree"]).exists()


def test_the_original_moves_only_when_clean_on_the_base(world):
    (world.repo / "mine.txt").write_text("사람의 것", encoding="utf-8")
    assert loop.forward(world.repo, "main").startswith("원본이 뒤처짐")
    assert not any(c[:2] == ["git", "merge"] for c in world.hub.calls)
    (world.repo / "mine.txt").unlink()
    git(world.repo, "checkout", "-q", "-b", "elsewhere")
    assert loop.forward(world.repo, "main").startswith("원본이 뒤처짐")


def test_the_remote_branch_goes_only_while_it_stands_on_the_merged_commit(world):
    spec = pr_spec(world, "fix-n", 7)
    approved = spec["pr"]["head"]
    world.hub.push_elsewhere(7)
    assert loop.pruned(world.repo, "fix-n", approved).startswith("원격 브랜치에 새 커밋 — 남김")
    assert git(world.origin, "branch", "--list", "fix-n")
    assert loop.pruned(world.repo, "fix-n", world.hub.head(7)) == "원격 브랜치 `fix-n` 를 지웠다"
    assert not git(world.origin, "branch", "--list", "fix-n")


def wrong_base(world, name: str, n: int) -> dict:
    spec = pr_spec(world, name, n)
    specs.update("proj", name, state="머지 대기", rounds=[{"n": 1, "head": spec["pr"]["head"], "base": "main",
                                                         "verdict": "allow", "findings": {"P0": 0, "P1": 0, "P2": 0}}])
    loop._cells[("proj", n)] = Reviewer(spec["worktree"], model="codex:test")
    world.hub.prs[n].update(state="MERGED", base="dev")
    loop.landed(world.repo, spec)
    return specs.load("proj", name)


def test_settle_accepts_or_opens_a_new_pr_with_a_fresh_review(world):
    web = client()
    pr_spec(world, "fix-o", 7)
    assert web.post("/api/specs/fix-o/settle", json={"choice": "accept"}).status_code == 409

    spec = wrong_base(world, "fix-p", 8)
    assert spec["stopped"]["reason"] == "검토하지 않은 base 에 머지됨" and Path(spec["worktree"]).exists()
    web.post("/api/specs/fix-p/settle", json={"choice": "accept"}).raise_for_status()
    spec = specs.load("proj", "fix-p")
    assert spec["state"] == "머지됨" and not Path(spec["worktree"]).exists()
    assert [r["text"] for r in chat.recall("next") if r["role"] == "result"][-1] == \
        "검토하지 않은 base `dev` 로 머지됨 — 받아들임"

    old = wrong_base(world, "fix-q", 9)
    specs.update("proj", "fix-q", extra=4)
    web.post("/api/specs/fix-q/settle", json={"choice": "reopen"}).raise_for_status()
    spec = specs.load("proj", "fix-q")
    assert spec["state"] == "작업 중" and spec["pr"] is None and spec["rounds"] == [] and spec["extra"] == 0
    assert spec["history"][-2]["pr"] == 9 and spec["history"][-2]["rounds"] == old["rounds"]
    assert spec["base"] == "main" and spec["branch"] == "fix-q"
    assert Path(spec["worktree"]).exists() and git(world.repo, "branch", "--list", "fix-q"), "지우지 않는다"
    assert ("proj", 9) not in loop._cells
    assert web.post("/api/specs/fix-q/settle", json={"choice": "reopen"}).status_code == 409

    # Stage 3's check opens the new pull request; the loop starts over at R1.
    world.hub.open(10, "fix-q")
    specs.update("proj", "fix-q", state="PR #10", pr={"number": 10, "url": "u", "base": "main",
                                                      "head": world.hub.head(10), "branch": "fix-q"})
    made = len(Reviewer.made)
    spec = looped("fix-q")
    assert spec["state"] == "머지 가능" and [r["n"] for r in spec["rounds"]] == [1]
    assert len(Reviewer.made) == made + 1 and order(world, 10, 1).startswith("Round 1 · PR #10")


def test_a_claude_review_cell_has_no_shell(world):
    """The evidence is the CLI's own arguments."""

    spec = pr_spec(world, "fix-r", 7)
    commands, real_popen = [], subprocess.Popen

    def spawn(command, **kwargs):
        if command[0] != "claude":
            return real_popen(command, **kwargs)
        commands.append(command)
        return real_popen(["python", "-c", "import sys; sys.stdin.read()"], **kwargs)

    with patch.object(loop, "ChatSession", chat_session.ChatSession), patch.object(loop, "review_model", lambda: "sonnet"), \
         patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        cell = loop.cell(spec, Path(spec["worktree"]))
        cell.ensure()
        cell.close()
    tools = commands[0][commands[0].index("--tools") + 1]
    allowed = commands[0][commands[0].index("--allowedTools") + 1]
    assert tools == allowed == "Read,Glob,Grep" and "Bash" not in tools
    assert commands[0][commands[0].index("--effort") + 1] == "high"


# -- the loop carries its repository ------------------------------------------------------------------


class Asking(Worker):
    """A work cell whose fix asks a person once, and waits for the answer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.answered = threading.Event()

    def say(self, text, halt=None):
        self.heard.append(text)
        if "Review round" not in text:
            yield Event("done", "알겠다", {"session_id": "cli-1", "error": False}, self.id)
            return
        yield Event("approval", "Write · fix.txt", {"id": "r1", "tool": "Write", "input": {}}, self.id)
        self.answered.wait(20)
        yield Event("done", fixed(("[P1] a.txt:1 — 틀렸다", "fixed"))(self.path, halt),
                    {"session_id": "cli-1", "error": False}, self.id)

    def answer(self, rid, allow, scope="once", answers=None):
        self.answered.set()
        return True


def test_a_switch_leaves_the_loop_and_its_approvals_where_they_are(world, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    other = _repo(other)
    repos = {"proj": world.repo, "other": other}
    web = client()
    pr_spec(world, "fix-s", 7)
    Reviewer.replies = [deny("[P1] a.txt:1 — 틀렸다"), allow]
    seen = len(work.feed.events)
    with patch.object(chat_channels, "repo_for", side_effect=repos.get), patch.object(work, "ChatSession", Asking):
        loop.kick("proj", "fix-s")
        spec = specs.load("proj", "fix-s")
        path = spec["worktree"]
        waited(lambda: work.waiting(path))
        web.post("/api/config/wiki", json={"repo": "other"}).raise_for_status()
        # The approval reaches the screens as the spec's `waiting`.
        waited(lambda: any(e.get("kind") == "spec" and e["id"] == "fix-s" and e["waiting"]
                           for e in work.feed.events[seen:]))
        assert web.get("/api/work/log", params={"path": path}).status_code == 200
        assert web.post("/api/work/say", json={"path": path, "text": "새 지시"}).status_code == 409
        session = work._sessions[path].id
        web.post("/api/work/answer", json={"path": path, "session_id": session, "id": "r1",
                                           "allow": True}).raise_for_status()
        waited(lambda: ("proj", "fix-s") not in loop._loops)
        # Idle now, and still not the selected project's: a new instruction is refused.
        assert web.post("/api/work/say", json={"path": path, "text": "새 지시"}).status_code == 404
    spec = specs.load("proj", "fix-s")
    assert spec["state"] == "머지 가능" and [r["verdict"] for r in spec["rounds"]] == ["deny", "allow"]


class Hangs:
    """A work cell whose turn runs until it is stopped."""

    id, parent_id, is_codex = "h", None, False

    def __init__(self):
        self.go = threading.Event()
        self.sent = []   # what reached the CLI: said with no halt set

    def say(self, text, halt=None):
        # As `ChatSession._say`: a halt set before the send stops the turn unsent.
        if halt is not None and halt.is_set():
            yield Event("error", "멈췄다.", {}, self.id)
            return
        self.sent.append(text)
        yield Event("tool", "x", {}, self.id)
        self.go.wait(10)
        yield Event("error", "프로세스가 닫혔다.", {}, self.id)

    def stop(self, halt):
        self.go.set()


@pytest.mark.parametrize("where", ["session", "begin"])
def test_a_stopped_loop_sends_no_turn(tmp_path, where):
    """The loop holds the worktree when the stop lands — before its run
    exists, or once the run is there and its thread not started. Either way
    nothing reaches the CLI."""

    spot, cell = loop.Loop("proj", "t1"), Hangs()
    real = work.begin

    def session(*args):
        if where == "session":
            spot.stop()
        return cell

    def begin(*args, **kwargs):
        if where == "begin":
            spot.stop()
        return real(*args, **kwargs)

    path = tmp_path / "proj-worktrees" / "t1"
    with patch.object(work, "session", session), patch.object(work, "begin", begin):
        assert loop.told(spot, {}, path, "x") is None
    assert cell.sent == [] and str(path) not in work._busy
