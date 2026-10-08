"""The review loop: rounds between a read-only review cell and the work cell,
the stops and what `[계속]` does for each, the pull request list and `adopt`,
and `[머지]` through the cleanup.
Both cells are stand-ins. GitHub is a stand-in behind `specs.sh`; git is real,
against a bare origin, so pushes, the squash merge and the lease on deleting
the remote branch all run through git itself."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import debt
from agent import chat_session
from agent.chat_session import Event
from main import channels as chat_channels
from main import app as main_app
from main import loop, specs, work
from main.specs import sh as git_command
from main import maintenance
from main import query as chat
from test_main import _repo, client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_specs import Worker
from workspace import adopt, create

GATE = "python gate.py"
FINAL = f"{GATE} && {debt.command()}"   # the final gate ends with the debt ratchet
INTEGRATION_WAIT = 120  # Completion ceiling, not a product performance or timeout contract.


def git(cwd: Path, *args: str) -> str:
    done = git_command(["git", "-C", str(cwd), *args], cwd)
    done.check_returncode()
    return done.stdout.strip()


def commit(path: Path, name: str, text: str = "x\n") -> str:
    (path / name).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", name)
    return git(path, "rev-parse", "HEAD")


def waited(test, seconds: float = INTEGRATION_WAIT) -> None:
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
        # A push leaves a loose ref: read it rather than start a git per `gh`
        # read. `rev-parse` still answers for one that was packed.
        loose = self.origin / "refs/heads" / self.prs[n]["branch"]
        return loose.read_text(encoding="utf-8").strip() if loose.is_file() else \
            git(self.origin, "rev-parse", f"refs/heads/{self.prs[n]['branch']}")

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

    def __init__(self, path, tools="", system="", model="", effort="", **rest):
        self.path, self.tools, self.model, self.effort, self.rest = Path(path), tools, model, effort, rest
        self.verification = rest.get("verification")
        self._env = rest.get("env", {})
        self.is_codex, self.session_id, self.alive, self.heard = model.startswith("codex:"), None, True, []
        self.id = uuid.uuid4().hex
        Reviewer.made.append(self)

    def reconfigure(self, model, effort):
        self.model, self.effort = model, effort

    def say(self, text, halt=None):
        self.heard.append(text)
        named = re.search(r"`([^`]+round-\d+\.md)`", text)
        first = Path(named[1]).read_text(encoding="utf-8").splitlines()[0] if named else ""
        reply = Reviewer.replies.pop(0) if Reviewer.replies else allow
        if callable(reply):
            reply = reply(first, text)
        self.session_id = f"review-{id(self)}"
        yield Event("done", reply, {"session_id": self.session_id, "error": False})

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


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """The project and its bare origin, built once. Never used in place:
    every test copies it (`world`), so nothing a test does reaches another."""

    top = tmp_path_factory.mktemp("template")
    origin = top / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = _repo(top)
    (repo / "gate.py").write_text("import os, sys\nsys.exit(1 if os.path.exists('broken') else 0)\n", encoding="utf-8")
    (repo / ".wiki").mkdir()
    (repo / ".wiki/adapter.toml").write_text(f'[slots]\ngate_cmd = "{GATE}"\n', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "gate")
    git(repo, "remote", "add", "origin", origin.as_posix())   # as written: `world` rewrites it
    git(repo, "push", "-q", "-u", "origin", "main")
    return top


@pytest.fixture
def world(tmp_path, template):
    origin, repo = tmp_path / "origin.git", tmp_path / "proj"
    shutil.copytree(template / "origin.git", origin)
    shutil.copytree(template / "proj", repo)
    config = repo / ".git/config"
    text = config.read_text(encoding="utf-8").replace((template / "origin.git").as_posix(), origin.as_posix())
    assert origin.as_posix() in text, "a push must never reach the template's origin"
    config.write_text(text, encoding="utf-8")
    hub = Hub(origin, tmp_path / "elsewhere")
    Reviewer.replies, Reviewer.made, Worker.replies, Worker.made = [], [], [], []
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Worker), \
         patch.object(loop, "ChatSession", Reviewer), patch.object(loop, "review_model", lambda: "codex:test"), \
         patch.object(specs, "sh", hub), patch.object(loop, "REVIEW", tmp_path / "review"), \
         patch.object(loop, "_cells", {}), patch.object(loop, "_loops", {}), patch.object(loop, "_review_runs", {}), \
         patch.object(loop, "_seated", 0):
        loop.store(auto_merge=False)  # Legacy round tests inspect the manual merge boundary.
        yield SimpleNamespace(repo=repo, hub=hub, origin=origin, tmp=tmp_path)
        for running in list(loop._loops.values()):
            running.stop()
            running.thread.join(10)
        work.close_all()


def pr_spec(w, name: str, n: int, file: str = "", **extra) -> dict:
    """A spec as stage 3 leaves it: the pull request up, its gate passed.
    Its one commit adds `file`, by default `<name>.txt`."""

    path = create(w.repo, name, linked=True)
    (path / (file or name)).parent.mkdir(parents=True, exist_ok=True)
    head = commit(path, file or f"{name}.txt")
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


@pytest.mark.parametrize("shared", [False, True])
def test_review_button_reuses_a_manually_published_task_pr(world, shared):
    sid = "migrate-postgresql-pgvector-reduced-embeddings"
    spec = pr_spec(world, sid, 11)
    if shared:
        git(world.repo, "worktree", "remove", spec["worktree"])
        git(world.repo, "checkout", "-q", sid)
        spec.update(worktree=str(world.repo), workspace_mode="branch")
    original = {key: spec[key] for key in ("goal", "done", "grounds", "rev", "source")}
    spec.update(pr=None, state="작업 중", branch=sid, gate=None)
    specs.save(spec)
    web = client()
    row = web.get("/api/prs").json()["rows"][0]
    assert row["spec"] == sid and row["pickable"]
    result = web.post("/api/loops", json={"prs": [11]}).json()["results"]
    assert result == [{"number": 11, "id": sid}]
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", sid)
    assert fresh["state"] == "머지 가능" and len(fresh["rounds"]) == 1
    assert fresh["pr"]["number"] == 11 and fresh["worktree"] == spec["worktree"]
    assert {key: fresh[key] for key in original} == original
    assert len(specs.listing("proj")) == 1
    # A repeated click follows the existing review path, without a new task.
    assert web.post("/api/loops", json={"prs": [11]}).json()["results"] == result
    waited(lambda: not loop._loops)


def test_external_pr_waits_for_click_even_with_legacy_auto_merge_enabled(world):
    repo = world.repo
    git(repo, "switch", "-c", "external-task")
    head = commit(repo, "external.txt")
    git(repo, "push", "-qu", "origin", "external-task")
    world.hub.open(12, "external-task")
    loop.store(auto_merge=True)
    response = client().post("/api/loops", json={"prs": [12]}).json()
    assert response["results"] == [{"number": 12, "id": "external-task"}]
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", "external-task")
    assert fresh["state"] == "머지 가능"
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)
    assert loop.settings()["auto_merge"] is False
    client().post("/api/specs/external-task/merge", json={"head": head}).raise_for_status()
    fresh = specs.load("proj", "external-task")
    assert fresh["state"] == "머지됨" and fresh["cleanup_complete"], fresh
    assert fresh["workspace_mode"] == "branch" and fresh["worktree"] == str(repo)
    assert git(repo, "branch", "--show-current") == "main" and (repo / "external.txt").exists()
    assert not git(repo, "branch", "--list", "external-task")
    assert not git(world.origin, "branch", "--list", "external-task")
    assert not loop.folder("proj", 12).exists()
    assert not loop._cells
    assert fresh["rounds"][0]["head"] == head


def test_adoption_preserves_a_dirty_checked_out_pr_and_does_not_create_metadata(world):
    git(world.repo, "switch", "-c", "dirty-pr")
    commit(world.repo, "published.txt")
    git(world.repo, "push", "-qu", "origin", "dirty-pr")
    world.hub.open(12, "dirty-pr")
    (world.repo / "notes.txt").write_text("keep\n", encoding="utf-8")
    result = client().post("/api/loops", json={"prs": [12]}).json()["results"][0]
    assert "커밋하지 않은 변경" in result["error"]
    assert specs.load("proj", "dirty-pr") is None
    assert (world.repo / "notes.txt").read_text(encoding="utf-8") == "keep\n"


def test_cleanup_retries_after_dirty_checkout_without_repeating_merge(world):
    spec = pr_spec(world, "retry-cleanup", 12)
    path = spec["worktree"]
    git(world.repo, "worktree", "remove", path)
    git(world.repo, "switch", "retry-cleanup")
    spec.update(workspace_mode="branch", worktree=str(world.repo))
    specs.save(spec)
    looped("retry-cleanup")
    ready = specs.load("proj", "retry-cleanup")
    real = world.hub
    def dirtied_after_merge(args, cwd, timeout=60):
        done = real(args, cwd, timeout)
        if args[:3] == ["gh", "pr", "merge"]:
            (world.repo / "notes.txt").write_text("keep\n", encoding="utf-8")
        return done
    with patch.object(specs, "sh", dirtied_after_merge):
        loop.merge_spec(world.repo, ready, loop.Merge(head=ready["pr"]["head"]))
    pending = specs.load("proj", "retry-cleanup")
    assert pending["state"] == "머지됨" and not pending["cleanup_complete"]
    assert loop.folder("proj", 12).exists() and (world.repo / "notes.txt").exists()
    assert git(world.repo, "branch", "--list", "retry-cleanup")
    with pytest.raises(Exception, match="동기화"):
        specs.checkout_idle(world.repo)
    (world.repo / "notes.txt").unlink()
    # A freshly loaded record is all restart recovery needs.
    loop.finish(world.repo, specs.load("proj", "retry-cleanup"), "main", pending["merge"]["commit"], "")
    done = specs.load("proj", "retry-cleanup")
    assert done["cleanup_complete"] and not loop.folder("proj", 12).exists()
    assert git(world.repo, "branch", "--show-current") == "main"
    assert not git(world.repo, "branch", "--list", "retry-cleanup")
    assert sum(args[:3] == ["gh", "pr", "merge"] for args in world.hub.calls) == 1


def test_legacy_pending_auto_merge_cannot_authorize_a_merge(world):
    spec = pr_spec(world, "automatic-race", 12)
    loop.store(auto_merge=True)
    specs.update("proj", spec["id"], auto_merge_pending=True)
    looped(spec["id"])
    fresh = specs.load("proj", spec["id"])
    loop.requested_merge(world.repo, fresh)
    assert fresh["state"] == "머지 가능" and len(fresh["rounds"]) == 1
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)


def test_merge_click_commits_wiki_indexes_and_omm_then_reviews_that_head(world):
    spec = pr_spec(world, "prepared-docs", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".omm/description.md", "# Architecture\n\nOld architecture.\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    original = specs.approved(ready)["head"]
    calls = []
    def scan(root, **kwargs):
        calls.append((root, kwargs.get("model")))
        (root / ".omm/description.md").write_text("# Architecture\n\nCurrent architecture.\n", encoding="utf-8")
    with patch.object(maintenance.architecture, "scan", scan):
        response = client().post(f"/api/specs/{spec['id']}/merge", json={"head": original})
        response.raise_for_status()
        waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "머지됨" and fresh["cleanup_complete"], fresh
    assert len(fresh["rounds"]) == 2
    prepared = fresh["maintenance"]["head"]
    assert prepared != original and fresh["rounds"][-1]["head"] == prepared
    assert fresh["validation"]["final"]["head"] == prepared
    command = next(c for c in world.hub.calls if c[:3] == ["gh", "pr", "merge"])
    assert command[-1] == prepared
    assert len(calls) == 1 and calls[0][0] == path
    files = git(world.repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert {".wiki/corpus.json", ".wiki/graph.json", ".omm/description.md"}.issubset(files)
    assert git(world.repo, "rev-parse", "main") == git(world.origin, "rev-parse", "main")
    assert not git(world.repo, "status", "--porcelain")


def test_merge_preparation_repairs_lint_before_committing(world):
    spec = pr_spec(world, "repair-wiki", 12, file="docs/feature.md")
    ready = looped(spec["id"])
    def problems(_repo, root):
        return [] if "Repaired" in (root / "docs/feature.md").read_text(encoding="utf-8") else [("lint", "Bad prose")]
    def repair(root, halt):
        (root / "docs/feature.md").write_text("# Repaired\n\nThe documented contract.\n", encoding="utf-8")
        return "Repaired the wiki prose."
    Worker.replies = [repair]
    with patch.object(maintenance, "findings", problems):
        client().post(f"/api/specs/{spec['id']}/merge", json={"head": ready["pr"]["head"]}).raise_for_status()
        waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "머지됨", fresh
    assert (world.repo / "docs/feature.md").read_text(encoding="utf-8").startswith("# Repaired")
    corpus = json.loads((world.repo / ".wiki/corpus.json").read_text(encoding="utf-8"))
    assert next(d for d in corpus["docs"] if d["path"] == "docs/feature.md")["title"] == "Repaired"


def test_merge_progress_is_readable_while_the_merge_request_is_waiting(world):
    spec = pr_spec(world, "visible-merge", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".omm/description.md", "# Architecture\n\nBefore scan.\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    reached, release = threading.Event(), threading.Event()
    responses = []

    def scan(root, **kwargs):
        reached.set()
        assert release.wait(120)

    def merge():
        responses.append(client().post(f"/api/specs/{spec['id']}/merge", json={"head": specs.approved(ready)["head"]}))

    with patch.object(maintenance.architecture, "scan", scan):
        worker = threading.Thread(target=merge)
        worker.start()
        try:
            assert reached.wait(120), [response.text for response in responses]
            rows = client().get("/api/specs").json()["specs"]
            visible = next(row for row in rows if row["id"] == spec["id"])["merge_progress"]
            assert visible["state"] == "running" and ".omm" in visible["stage"]
            assert any("색인" in step["text"] for step in visible["steps"])
            assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)
        finally:
            release.set()
            worker.join(30)
        waited(lambda: not loop._loops)
    assert responses[0].status_code == 200
    done = specs.load("proj", spec["id"])
    assert done["merge_progress"]["state"] == "completed" and done["cleanup_complete"]
    assert any("origin/main" in step["text"] for step in done["merge_progress"]["steps"])


@pytest.mark.parametrize("failure", ["dirty", "scan", "lint"])
def test_failed_merge_preparation_preserves_files_and_never_merges(world, failure):
    spec = looped(pr_spec(world, "failed-preparation", 12)["id"])
    path = Path(spec["worktree"])
    if failure == "dirty":
        (path / "personal.txt").write_text("Preserve this", encoding="utf-8")
    if failure == "scan":
        (path / ".omm").mkdir()
    with patch.object(maintenance.architecture, "scan", side_effect=ValueError("Scan failed")), \
         patch.object(maintenance, "findings", return_value=[("lint", "Unresolved")] if failure == "lint" else []), \
         patch.object(maintenance, "repair"):
        response = client().post(f"/api/specs/{spec['id']}/merge", json={"head": spec["pr"]["head"]})
    assert response.status_code == 409
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)
    assert not specs.load("proj", spec["id"]).get("merge_request")
    if failure == "dirty":
        assert (path / "personal.txt").read_text(encoding="utf-8") == "Preserve this"


def test_merge_intent_does_not_follow_a_later_repair_head(world):
    spec = looped(pr_spec(world, "request-scope", 12)["id"])
    specs.update("proj", spec["id"], merge_request={"head": "0" * 40, "base": "main", "rev": 1, "pr": 12})
    loop.requested_merge(world.repo, specs.load("proj", spec["id"]))
    assert not specs.load("proj", spec["id"])["merge_request"]
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)


@pytest.mark.parametrize("repair,merges", [("docs/fixed.md", True), (".omm/fixed/description.md", True),
                                           ("fixed.py", False)])
def test_merge_intent_follows_a_reviewed_document_repair_of_the_prepared_head(world, repair, merges):
    spec = looped(pr_spec(world, "request-repair", 12)["id"])
    prepared = specs.approved(spec)["head"]
    specs.update("proj", spec["id"], merge_request={"head": prepared, "base": "main", "rev": 1, "pr": 12})
    path = Path(spec["worktree"])
    (path / repair).parent.mkdir(parents=True, exist_ok=True)
    repaired = commit(path, repair, "# Fixed\n\nThe reviewer's correction.\n")
    git(path, "push", "origin", spec["id"])
    done = looped(spec["id"])
    merged = [c for c in world.hub.calls if c[:3] == ["gh", "pr", "merge"]]
    if merges:
        assert done["state"] == "머지됨" and merged[-1][-1] == repaired, done
    else:
        assert done["state"] == "머지 가능" and not merged and not done["merge_request"]
        assert "머지 요청 취소" in done["merge_progress"]["stage"]


def test_a_source_renamed_into_a_document_is_not_a_document_repair(world):
    spec = pr_spec(world, "rename-repair", 12, file="app.py")
    path = Path(spec["worktree"])
    before = git(path, "rev-parse", "HEAD")
    (path / "docs").mkdir()
    git(path, "mv", "app.py", "docs/retired.md")
    git(path, "commit", "-qm", "rename")
    assert not maintenance.documents_only(path, before, git(path, "rev-parse", "HEAD"))


def test_an_invalid_architecture_answer_keeps_the_documents_and_merges(world):
    spec = pr_spec(world, "invalid-omm", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".omm/description.md", "# Architecture\n\nReviewed.\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    failed = maintenance.architecture.Invalid("Every diagram component requires a described child")
    with patch.object(maintenance.architecture, "scan", side_effect=failed):
        client().post(f"/api/specs/{spec['id']}/merge", json={"head": specs.approved(ready)["head"]}).raise_for_status()
        waited(lambda: not loop._loops)
    done = specs.load("proj", spec["id"])
    assert done["state"] == "머지됨", done
    assert (world.repo / ".omm/description.md").read_text(encoding="utf-8") == "# Architecture\n\nReviewed.\n"
    assert any(".omm 구조 문서는 그대로" in step["text"] for step in done["merge_progress"]["steps"])


@pytest.mark.parametrize("stage", ["scan", "lint"])
def test_interrupted_maintenance_retries_saved_outputs_after_a_new_click(world, stage):
    spec = pr_spec(world, "interrupted-maintenance", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".omm/description.md", "# Architecture\n\nBefore scan.\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    head = specs.approved(ready)["head"]

    def interrupt(*args, **kwargs):
        if stage == "lint":
            (path / "docs/feature.md").write_text("# Repaired\n\nSaved before interruption.\n", encoding="utf-8")
        raise ValueError("Interrupted maintenance")

    with patch.object(maintenance, "findings", return_value=[("lint", "Bad prose")] if stage == "lint" else []), \
         patch.object(maintenance, "repair", interrupt), \
         patch.object(maintenance.architecture, "scan", interrupt):
        response = client().post(f"/api/specs/{spec['id']}/merge", json={"head": head})
    assert response.status_code == 409
    saved = specs.load("proj", spec["id"])
    assert saved["maintenance"]["state"] == "interrupted" and not saved.get("merge_request")
    assert (path / ".wiki/corpus.json").exists()
    loop.requested_merge(world.repo, saved)
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)
    with patch.object(maintenance, "findings", return_value=[]), patch.object(maintenance.architecture, "scan"):
        client().post(f"/api/specs/{spec['id']}/merge", json={"head": head}).raise_for_status()
        waited(lambda: not loop._loops)
    done = specs.load("proj", spec["id"])
    assert done["state"] == "머지됨" and len(done["rounds"]) == 2, done
    assert done["validation"]["final"]["head"] == done["maintenance"]["head"] != head
    if stage == "lint":
        assert (world.repo / "docs/feature.md").read_text(encoding="utf-8").startswith("# Repaired")


@pytest.mark.parametrize("change", ["user_file", "generated_file", "staging", "rev", "base", "pr", "source_head"])
def test_interrupted_maintenance_never_adopts_changed_outputs_or_identity(world, change):
    spec = pr_spec(world, "interrupted-scope", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".omm/description.md", "# Architecture\n\nBefore scan.\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    head = specs.approved(ready)["head"]
    with patch.object(maintenance.architecture, "scan", side_effect=ValueError("Scan interrupted")):
        with pytest.raises(ValueError, match="Scan interrupted"):
            maintenance.prepare(world.repo, path, ready, head, "main")
    saved = specs.load("proj", spec["id"])
    if change == "user_file":
        (path / "personal.md").write_text("# Personal\n\nPreserve me.\n", encoding="utf-8")
    elif change == "generated_file":
        (path / ".wiki/corpus.json").write_text('{"keep": true}', encoding="utf-8")
    elif change == "staging":
        git(path, "add", "-f", ".wiki/corpus.json")
    else:
        saved["maintenance"][change] = "another" if change == "base" else 99
        if change == "source_head":
            saved["maintenance"]["head"] = "0" * 40
    before = maintenance.outputs(path)
    with pytest.raises(ValueError, match="커밋하지 않은"):
        maintenance.prepare(world.repo, path, saved, head, "main")
    assert maintenance.outputs(path) == before
    assert git(path, "rev-parse", "HEAD") == head


def test_completed_preparation_is_reused_and_ignored_documents_stay_private(world):
    spec = pr_spec(world, "reuse-preparation", 12, file="docs/feature.md")
    path = Path(spec["worktree"])
    (path / ".omm").mkdir()
    commit(path, ".gitignore", "docs/private.md\n")
    git(path, "push", "origin", spec["id"])
    ready = looped(spec["id"])
    (path / "docs/private.md").write_text("# Private\n\nPRIVATE_SENTINEL", encoding="utf-8")
    with patch.object(maintenance.architecture, "scan") as scan:
        prepared = maintenance.prepare(world.repo, path, ready, specs.approved(ready)["head"], "main")
        saved = specs.load("proj", spec["id"])
        assert maintenance.prepare(world.repo, path, saved, prepared, "main") == prepared
    scan.assert_called_once()
    assert "PRIVATE_SENTINEL" not in (path / ".wiki/corpus.json").read_text(encoding="utf-8")
    assert "docs/private.md" not in git(path, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_stopping_a_lint_repair_does_not_stop_the_server(world):
    spec = pr_spec(world, "cancel-maintenance", 12)
    path = Path(spec["worktree"])
    from main import runtime

    def cancelled(root, halt):
        halt.set()
        return "Stopped this repair."
    Worker.replies = [cancelled]
    with pytest.raises(ValueError, match="완료되지"):
        maintenance.repair(path, spec, [("lint", "A finding")])
    assert not runtime.stopping.is_set()


def test_refresh_removes_the_last_deleted_document_from_existing_indexes(world):
    document = world.repo / "README.md"
    document.write_text("# Last document\n", encoding="utf-8")
    maintenance.indexes(world.repo)
    document.unlink()
    maintenance.indexes(world.repo)
    corpus = json.loads((world.repo / ".wiki/corpus.json").read_text(encoding="utf-8"))
    graph = json.loads((world.repo / ".wiki/graph.json").read_text(encoding="utf-8"))
    assert not corpus["docs"] and graph["counts"]["docs"] == 0


def test_forward_updates_main_when_it_is_open_in_another_checkout(world):
    spec = pr_spec(world, "shared-base", 12)
    path = Path(spec["worktree"])
    world.hub.squash(12)
    git(world.repo, "fetch", "origin")
    assert loop.forward(path, "main", task_branch=spec["id"]).startswith("원본을")
    assert git(world.repo, "rev-parse", "main") == git(world.origin, "rev-parse", "main")


def test_forward_preserves_unpublished_main_and_reports_pending(world):
    unpublished = commit(world.repo, "unpublished.txt")
    assert loop.forward(world.repo, "main").startswith("원본이 뒤처짐")
    assert git(world.repo, "rev-parse", "HEAD") == unpublished


@pytest.mark.parametrize("environment", ["external", "claude-cloud"])
def test_manual_pr_attachment_preserves_external_implementation_ownership(world, environment):
    spec = pr_spec(world, "external-manual-pr", 11, implementation_environment=environment)
    spec.update(pr=None, state="작업 중", branch=spec["id"])
    specs.save(spec)
    with patch.object(loop, "kick") as dispatch:
        result = client().post("/api/loops", json={"prs": [11]}).json()["results"]
    assert result == [{"number": 11, "id": spec["id"]}]
    assert specs.load("proj", spec["id"])["implementation_environment"] == environment
    dispatch.assert_called_once_with("proj", spec["id"])


def test_review_attach_refuses_a_busy_or_wrong_checkout_without_changing_metadata(world):
    sid = "manual-pr"
    spec = pr_spec(world, sid, 11)
    spec.update(pr=None, state="작업 중", branch=sid)
    specs.save(spec)
    path = spec["worktree"]
    with patch.dict(work._busy, {path: object()}):
        result = client().post("/api/loops", json={"prs": [11]}).json()["results"][0]
    assert "실행 중" in result["error"] and specs.load("proj", sid)["pr"] is None
    git(Path(path), "checkout", "-q", "--detach")
    result = client().post("/api/loops", json={"prs": [11]}).json()["results"][0]
    assert "브랜치" in result["error"] and specs.load("proj", sid)["pr"] is None
    assert not loop._loops


def looped(name: str, seconds: float = INTEGRATION_WAIT) -> dict:
    """Start the spec's loop and wait until it has stopped driving."""

    loop.kick("proj", name)
    waited(lambda: ("proj", name) not in loop._loops, seconds)
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
    table = plan.split("| Reason for stopping | Restart |")[1].split("\n\n")[0]
    listed = re.findall(r"^\| `([^`]+)` \|", table, re.M)
    assert listed == [w.value for w in loop.Why]
    with pytest.raises(ValueError, match="표에 없는"):
        loop.stop(None, "proj", "x", "승인 대기")


# -- review profiles and finding identity (`docs/plans/reliability/6-review-profiles.md`) ---------


def meta(*entries) -> str:
    """A `finding-meta` block, one `(component, invariant[, existing_id])` per finding."""

    return "```finding-meta\n" + json.dumps(
        [{"ordinal": i, "existing_id": e[2] if len(e) > 2 else None, "component": e[0], "invariant": e[1],
          "trigger": "t", "evidence": "e"} for i, e in enumerate(entries, 1)]) + "\n```"


def denied(findings: list[str], block: str):
    return lambda first, _text: f"{first}\n" + "\n".join(findings) + f"\n{block}\n머지 불가 — 고칠 것이 있다"


def test_finding_meta_is_checked_against_the_ids_the_spec_has():
    head = "abcdef0123456789abcdef0123456789abcdef01"

    def text(block):
        return f"Round 1 · PR #12 · abcdef0\n[P1] a.py:1 — x\n[P2] b.py:2 — y\n{block}\n머지 불가 — P1"

    parsed = loop.parse(text(meta(("loop.merge", "gate on head"), ("ui", "label", "F1"))), 1, 12, head, {"F1"})
    assert parsed["identity"] == "full" and parsed["findings"][1]["meta"]["existing_id"] == "F1"
    assert parsed["findings"][1]["body"] == "", "블록은 발견의 본문이 아니다"
    for block, why in ((meta(("a", "b"), ("c", "d", "F2")), "F2"), (meta(("a", "b"), ("c", "d", 1)), "existing_id"),
                       (meta(("a", "b")), "ordinal"), ("```finding-meta\n{not json\n```", "JSON"),
                       (meta(("a", " "), ("c", "d")), "invariant"),
                       ("```finding-meta\n{not json", "닫히지 않았다")):
        with pytest.raises(ValueError, match=why):
            loop.parse(text(block), 1, 12, head, {"F1"})
    legacy = loop.parse(text(""), 1, 12, head)
    assert legacy["identity"] == "limited" and "meta" not in legacy["findings"][0], "블록 없는 답도 읽는다"
    entries = [{"ordinal": 1, "component": "loop.merge", "invariant": "gate on head", "existing_id": "F1"},
               {"ordinal": 2, "limited": True}]
    partial = loop.parse(text("```finding-meta\n" + json.dumps(entries) + "\n```"), 1, 12, head, {"F1"})
    assert partial["identity"] == "limited" and partial["findings"][0]["meta"]["existing_id"] == "F1"
    assert "meta" not in partial["findings"][1]
    for entry in ({"ordinal": 2, "limited": False}, {"ordinal": 2, "limited": 1},
                  {"ordinal": 2, "limited": True, "existing_id": "F1"}):
        with pytest.raises(ValueError, match="limited"):
            loop.parse(text("```finding-meta\n" + json.dumps([entries[0], entry]) + "\n```"), 1, 12, head, {"F1"})
    assert loop.parse("Round 1 · PR #12 · abcdef0\n새 발견 없음\n머지 허용", 1, 12, head)["identity"] == "full"


def test_ids_follow_the_validated_id_then_the_invariant_and_flag_a_near_miss():
    def found(*metas):
        return [{"grade": "P1", "file": "a.py", "line": i, "head": f"[P1] a.py:{i} — x", "body": "", "meta": m}
                for i, m in enumerate(metas)]

    def m(component, invariant, existing=None):
        return {"component": component, "invariant": invariant, "existing_id": existing}

    spec = {"rounds": []}
    spec["rounds"].append({"n": 1, "items": loop.identified(spec, found(m("loop.merge", "Final gate matches HEAD")))})
    again = loop.identified(spec, found(m("Loop.merge", " final gate  matches head"), m("loop.merge", "base read now"),
                                        m("ui", "other words", "F1")))
    assert [f["id"] for f in again] == ["F1", "F2", "F1"], "같은 불변식은 같은 id, 검증된 existing_id 가 먼저"
    assert again[1]["possible"] == "F1" and again[0]["possible"] is None
    spec["rounds"].append({"n": 2, "items": again})
    assert loop.seen(spec) == {"F1": [1, 2], "F2": [2]}
    unnamed = loop.identified(spec, [{**found(m("a", "b"))[0], "meta": None}])
    assert unnamed[0]["id"] is None, "meta 없는 발견은 id 를 지어내지 않는다"


def test_a_disposition_id_counts_only_when_this_round_gave_it_to_that_finding():
    """PR #56 round 1: an id the fixing side made up, repeated over two
    rounds, stopped the loop for a dispute between unrelated findings."""

    def said(finding, fid):
        return loop.disposed("```disposition\n" + json.dumps(
            [{"finding": finding, "id": fid, "action": "disagree"}]) + "\n```")

    items = [{"id": "F1", "grade": "P1", "head": "[P1] a.py:1 — issue A"},
             {"id": "F2", "grade": "P1", "head": "[P1] b.py:70 — issue B"}]
    before = loop.vouched(said("[P1] a.py:1 — issue A", "F999"), items[:1])
    now = loop.vouched(said("[P1] b.py:70 — unrelated issue B", "F999"), items[1:])
    assert before[0]["id"] is None and now[0]["id"] is None, "준 적 없는 id 는 버린다"
    assert loop.disputed(before, now) == "", "다른 발견은 반론 반복이 아니다"
    assert loop.vouched(said("[P1] b.py:70 — issue B", "F1"), items)[0]["id"] is None, "다른 발견의 id"
    kept = loop.vouched(said("[P1] a.py:1 — reworded", "F1"), items)
    assert kept[0]["id"] == "F1" and loop.vouched(None, items) is None
    assert loop.disputed(kept, loop.vouched(said("[P1] a.py:9 — moved", "F1"), items)) == "[P1] a.py:9 — moved"
    # Round 2: two findings at one place. F2's own id stands, and a first
    # disagreement with F2 is not a repeat of the one with F1.
    shared = [{"id": "F1", "grade": "P1", "head": "[P1] a.py:1 — validate input"},
              {"id": "F2", "grade": "P1", "head": "[P1] a.py:1 — close connection"}]
    again = loop.vouched(said("[P1] a.py:1 — close connection", "F2"), shared)
    assert again[0]["id"] == "F2"
    assert loop.disputed(loop.vouched(said("[P1] a.py:1 — validate input", "F1"), shared), again) == ""
    misplaced = loop.vouched(said("[P1] a.py:1 — validate input", "F2"), shared)
    assert misplaced[0]["id"] is None
    completed = loop.settled(shared, misplaced)
    assert [f["disposition"] for f in completed] == ["disagree", None]
    ambiguous = loop.vouched(said("[P1] a.py:1 — reworded", None), shared)
    assert all(f["disposition"] is None for f in loop.settled(shared, ambiguous))


def test_the_same_change_reviewed_as_plan_or_code_gets_its_own_criteria(world):
    spec = pr_spec(world, "plan-a", 7, file="docs/plans/a/1-x.md", review_profile="plan", artifact_root="docs/plans/a")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    oid = specs.current_merge_base(path, "main", head)
    paths = loop.changed(path, oid, head)
    assert paths == ["docs/plans/a/1-x.md"] and loop.effective(spec, paths) == "plan"
    assert loop.effective({**spec, "review_profile": None}, paths) == "code", "`.md` 만으로 plan 이 되지 않는다"
    for other in (["docs/plans/a/x.py"], ["docs/plans/b/1.md"], None):
        assert loop.effective(spec, other) == "mixed", other
    plan = loop.instruction(spec, path, 1, head, "main", True, "plan", oid)
    assert "## Plan criteria" in plan and "## Code criteria" not in plan
    assert "planned code is not written yet" in " ".join(plan.split())
    assert f"- R1: {GATE}" in plan and "## Source manifest" in plan and f"merge base `{oid}`" in plan
    code = loop.instruction({**spec, "review_profile": "code"}, path, 1, head, "main", True)
    assert "## Code criteria" in code and "## Plan criteria" not in code and "## Requirements" not in code
    mixed = loop.instruction(spec, path, 1, head, "main", True, "mixed", oid)
    assert mixed.count("## Plan criteria") == mixed.count("## Code criteria") == 1
    assert "the change reaches past it" in mixed


def test_a_plan_round_refuses_a_missing_acceptance_under_the_plan_criteria(world):
    pr_spec(world, "plan-b", 7, file="docs/plans/b/1-x.md", review_profile="plan", artifact_root="docs/plans/b")
    finding = "[P1] docs/plans/b/1-x.md:3 — R1 has no observable acceptance"
    Reviewer.replies = [denied([finding], meta(("plan.acceptance", "every requirement has observable acceptance"))),
                        allow, keep()]
    Worker.replies = [fixed((finding, "fixed"), name="docs/plans/b/2-acceptance.md")]
    spec = looped("plan-b")
    assert spec["state"] == "머지 가능"
    assert [(r["profile"], r["verdict"]) for r in spec["rounds"]] == [("plan", "deny"), ("plan", "allow")]
    first = spec["rounds"][0]
    assert first["identity"] == "full" and first["items"][0]["id"] == "F1"
    assert first["items"][0]["disposition"] == "fixed" and first["items"][0]["component"] == "plan.acceptance"
    assert "id: `F1`" in Worker.made[-1].heard[0]
    second = order(world, 7, 2)
    assert "## Code criteria" not in second and "`F1` · P1 · plan.acceptance" in second
    assert "last disposition `fixed`" in second


def test_code_outside_the_plan_root_cannot_skip_the_code_criteria(world):
    spec = pr_spec(world, "plan-c", 7, file="docs/plans/c/1-x.md", review_profile="plan", artifact_root="docs/plans/c")
    path = Path(spec["worktree"])
    commit(path, "tool.py")
    git(path, "push", "-q", "origin", "plan-c")
    Reviewer.replies = [allow, keep()]
    spec = looped("plan-c")
    assert spec["state"] == "머지 가능" and spec["rounds"][0]["profile"] == "mixed"
    text = order(world, 7, 1)
    assert "## Code criteria" in text and "## Plan criteria" in text


def test_an_id_the_server_never_gave_is_sent_back_once_then_stops(world):
    pr_spec(world, "fix-u", 7)
    made_up = denied(["[P1] a.txt:1 — 틀렸다"], meta(("a.txt", "value is right", "F9")))
    Reviewer.replies = [made_up, made_up]
    spec = looped("fix-u")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == "라운드 형식" and "F9" in spec["stopped"]["detail"]
    assert "F9" in Reviewer.made[0].heard[1] and "Known findings" in Reviewer.made[0].heard[1]
    assert not spec.get("rounds"), "형식이 틀린 라운드는 남지 않는다"


def test_a_stale_round_neither_raises_nor_repeats_a_finding(world):
    pr_spec(world, "fix-s", 7)
    finding = "[P1] a.txt:1 — 틀렸다"

    def pushed_meanwhile(first, text):
        world.hub.push_elsewhere(7)
        return denied([finding], meta(("a.txt", "value is right")))(first, text)

    Reviewer.replies = [pushed_meanwhile, denied([finding], meta(("a.txt", "value is right"))),
                        denied([finding], meta(("a.txt", "value is right", "F1"))), allow, keep()]
    Worker.replies = [fixed((finding, "fixed")), fixed((finding, "fixed"))]
    # Several reviews and two real Git repair/push cycles take almost 30s
    # on this Windows host; leave startup margin without weakening assertions.
    spec = looped("fix-s")
    assert spec["state"] == "머지 가능"
    assert [r.get("stale", False) for r in spec["rounds"]] == [True, False, False, False]
    assert "items" not in spec["rounds"][0]
    assert loop.seen(spec) == {"F1": [1, 2]}, "버린 라운드는 반복으로 세지 않는다"


def test_a_round_from_before_profiles_still_reads_and_carries_no_identity(world):
    spec = pr_spec(world, "fix-l", 7)
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    legacy = {"n": 1, "head": head, "base": "main", "verdict": "deny", "findings": {"P0": 0, "P1": 1, "P2": 0},
              "gate": {"ok": True, "cmd": GATE, "head": head}, "ts": 1.0,
              "disposition": [{"finding": "[P1] a.txt:1 — x", "action": "fixed", "evidence": ""}]}
    spec = specs.update("proj", "fix-l", rounds=[legacy])
    assert loop.issues(spec) == {} and loop.seen(spec) == {}
    text = loop.instruction(spec, path, 2, head, "main", True)
    assert "Profile `code`" in text and "(none with an id yet)" in text and '"action": "fixed"' in text
    assert specs.view(world.repo, spec)["review_profile"] == "code"
    Reviewer.replies = [allow, keep()]
    spec = looped("fix-l")
    assert spec["state"] == "머지 가능" and spec["rounds"][0] == legacy, "옛 라운드는 고쳐 쓰지 않는다"
    assert spec["rounds"][1]["profile"] == "code"


def test_the_reviewer_is_its_own_read_only_session_and_a_setting_applies_next_round(world):
    pr_spec(world, "fix-w", 7)
    finding, current = "[P1] a.txt:1 — 틀렸다", ["codex:first"]

    def fix_and_change(path, halt):
        current[0] = "codex:second"   # changed while the round's fix runs
        return fixed((finding, "fixed"))(path, halt)

    Reviewer.replies = [deny(finding), allow, keep()]
    Worker.replies = [fix_and_change]
    with patch.object(loop, "review_model", lambda: current[0]):
        spec = looped("fix-w")
    one, two = (r["reviewer"] for r in spec["rounds"])
    assert (one["model"], two["model"]) == ("codex:first", "codex:second"), "판정마다 그때의 모델"
    assert len(Reviewer.made) == 1 and one["cell"] == two["cell"] and one["session_id"]
    assert one["tools"] == loop.REVIEW_TOOLS and not Reviewer.made[0].rest, "리뷰 셀은 쓰기 권한을 받지 않는다"
    assert one["cell"] not in {w.id for w in Worker.made} and one["session_id"] != "cli-1", "작업 셀과 다른 세션"


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


def test_a_spec_start_made_runs_its_first_round(world):
    """`[시작]` makes a spec with no `rounds` (`specs.card`). Read as
    `spec["rounds"]`, round 1 broke the loop — PR #27 stopped at `KeyError`."""

    pr_spec(world, "fix-r", 7)
    spec = specs.load("proj", "fix-r")
    del spec["rounds"]
    specs.save(spec)
    Reviewer.replies = [allow]
    assert looped("fix-r")["state"] == "머지 가능"


def test_a_pr_the_plan_row_left_behind_goes_into_review_from_the_list(world):
    """The row's turn failed after the pull request went up: nothing kicked the
    loop, and it was neither `멈춤` for `[계속]` nor pickable in the list."""

    pr_spec(world, "fix-p", 7, fault="계획 행을 고칠 턴을 보내지 못했다")
    web = client()
    assert next(r for r in web.get("/api/prs").json()["rows"] if r["number"] == 7)["pickable"]
    Reviewer.replies = [allow]
    assert web.post("/api/loops", json={"prs": [7]}).json()["results"] == [{"number": 7, "id": "fix-p"}]
    waited(lambda: not loop._loops)
    spec = specs.load("proj", "fix-p")
    assert spec["state"] == "머지 가능" and spec["fault"] is None
    pr_spec(world, "fix-q", 8)
    assert loop.refusal({}, specs.load("proj", "fix-q")) == "이미 PR #8", "잘못 없이 도는 PR 은 그대로"
    pr_spec(world, "fix-r", 9, fault="계획 행 커밋의 push 실패", plan_commit="asked")
    assert not loop.stranded(specs.load("proj", "fix-r")), "계획 행이 빠진 머리를 리뷰하지 않는다"
    assert "계획 행 커밋이 아직" in loop.refusal({}, specs.load("proj", "fix-r"))


def test_deleting_a_worktree_takes_its_unmerged_spec_off_the_rail(world):
    """Kept, the spec stood on the rail with no worktree under it."""

    web = client()
    spec = pr_spec(world, "fix-d", 7)
    specs.update("proj", "fix-d", state="멈춤", stopped={"reason": "사람이 멈춤", "detail": ""})
    web.post("/api/worktrees/remove", json={"path": spec["worktree"], "force": True}).raise_for_status()
    assert specs.load("proj", "fix-d") is None
    assert list((specs.SPECS / "proj" / "dropped").glob("fix-d.*.json")), "지우지 않고 치운다"
    assert "fix-d" not in [s["id"] for s in web.get("/api/specs").json()["specs"]]
    assert next(r for r in web.get("/api/prs").json()["rows"] if r["number"] == 7)["pickable"], "PR 은 남는다"
    merged = pr_spec(world, "fix-e", 8)
    specs.update("proj", "fix-e", state="머지됨")
    web.post("/api/worktrees/remove", json={"path": merged["worktree"], "force": True}).raise_for_status()
    assert specs.load("proj", "fix-e")["state"] == "머지됨", "머지된 명세는 P2 를 들고 남는다"


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


@pytest.mark.parametrize("early", ["", "Tests are still running.", "claimed-fixed", "partial", "unsupported-disagree", "errored"])
def test_the_next_review_waits_for_a_complete_correction_and_published_head(world, monkeypatch, early):
    spec = pr_spec(world, "repair-pending", 7)
    finding = "[P1] a.txt:1 — incomplete repair"
    other = "[P1] b.txt:2 — another pending finding"
    if early == "claimed-fixed":
        early = "```disposition\n" + json.dumps([
            {"finding": f, "action": "fixed", "evidence": "claimed done"} for f in (finding, other)]) + "\n```"
    elif early == "partial":
        early = fixed((finding, "fixed"))
    elif early == "unsupported-disagree":
        early = "```disposition\n" + json.dumps([
            {"finding": f, "action": "disagree", "evidence": ""} for f in (finding, other)]) + "\n```"
    elif early == "errored":
        early = fixed((finding, "fixed"), (other, "fixed"))
        say = Worker.say

        def interrupted(worker, text, halt=None):
            if not worker.heard:
                yield Event("error", "Provider stream interrupted", {}, worker.id)
            yield from say(worker, text, halt)

        monkeypatch.setattr(Worker, "say", interrupted)
    entered, release = threading.Event(), threading.Event()

    def finish_repair(path, halt):
        entered.set()
        assert release.wait(120), "The fixture did not finish the correction"
        return fixed((finding, "fixed"), (other, "fixed"))(path, halt)

    Reviewer.replies = [deny(finding, other), allow]
    Worker.replies = [early, finish_repair]
    loop.kick("proj", spec["id"])
    try:
        waited(lambda: entered.is_set() or not loop._loops, seconds=15)
        assert entered.is_set(), "The loop advanced without continuing the incomplete correction"
        pending = specs.load("proj", spec["id"])
        assert pending["state"] == "고치는 중 R1" and len(pending["rounds"]) == 1
        assert not (loop.folder("proj", 7) / "round-2.md").exists()
        assert world.hub.head(7) == spec["pr"]["head"]
        assert len(Reviewer.made[0].heard) == 1
    finally:
        release.set()
        waited(lambda: not loop._loops)
    complete = specs.load("proj", spec["id"])
    assert complete["state"] == "머지 가능" and len(complete["rounds"]) == 2
    assert complete["rounds"][1]["head"] != complete["rounds"][0]["head"]
    assert complete["rounds"][1]["head"] == world.hub.head(7)


@pytest.mark.parametrize("stored", ["pending", "partial", "stale"])
def test_resume_finishes_the_pending_correction_before_requesting_another_review(world, stored):
    spec = pr_spec(world, "repair-resume", 7)
    finding = "[P1] a.txt:1 — incomplete repair"
    Reviewer.replies = [deny(finding), allow]
    Worker.replies = ["", ""]
    pending = looped(spec["id"])
    assert pending["state"] == "멈춤" and len(pending["rounds"]) == 1
    assert pending["rounds"][0]["disposition"] is None
    assert not (loop.folder("proj", 7) / "round-2.md").exists()
    stale = None
    if stored == "partial":
        specs.update("proj", spec["id"], rounds=[{**pending["rounds"][0], "disposition": []}])
    elif stored == "stale":
        stale = {**pending["rounds"][0], "n": 2, "stale": True}
        specs.update("proj", spec["id"], rounds=[*pending["rounds"], stale])
    Worker.replies = [fixed((finding, "fixed"))]
    client().post(f"/api/specs/{spec['id']}/resume", json={}).raise_for_status()
    waited(lambda: not loop._loops)
    complete = specs.load("proj", spec["id"])
    assert complete["state"] == "머지 가능" and len(loop.counted(complete)) == 2
    assert "round 1" in Worker.made[-1].heard[-1]
    assert loop.counted(complete)[1]["head"] != complete["rounds"][0]["head"]
    if stale:
        assert complete["rounds"][1] == stale and len(complete["rounds"]) == 3


@pytest.mark.parametrize("identity", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_same_line_findings_need_separate_correction_reports_before_the_next_review(world, identity, ambiguous):
    spec = pr_spec(world, "same-line-repair", 7)
    findings = ["[P1] a.py:10 — validate input", "[P1] a.py:10 — close connection"]
    Reviewer.replies = [denied(findings, meta(("a", "input"), ("a", "connection"))) if identity else deny(*findings), allow]

    def report(entries):
        return "```disposition\n" + json.dumps([
            {"finding": f, "action": "not-reproduced", "evidence": f"Ran the trigger for {f}"} for f in entries]) + "\n```"

    entered, release = threading.Event(), threading.Event()

    def complete(_path, _halt):
        entered.set()
        assert release.wait(120)
        return report(findings)

    Worker.replies = [report(["[P1] a.py:10 — this location" if ambiguous else findings[0]]), complete]
    loop.kick("proj", spec["id"])
    try:
        waited(lambda: entered.is_set() or not loop._loops)
        assert entered.is_set(), "One location-based disposition incorrectly completed two findings"
        pending = specs.load("proj", spec["id"])
        assert pending["state"] == "고치는 중 R1" and len(pending["rounds"]) == 1
        assert not (loop.folder("proj", 7) / "round-2.md").exists()
        assert world.hub.head(7) == spec["pr"]["head"]
    finally:
        release.set()
        waited(lambda: not loop._loops)
    finished = specs.load("proj", spec["id"])
    assert finished["state"] == "머지 가능" and len(finished["rounds"]) == 2
    assert all(f["disposition"] == "not-reproduced" for f in finished["rounds"][0]["items"])
    assert world.hub.head(7) == spec["pr"]["head"]


@pytest.mark.parametrize("action", ["disagree", "not-reproduced"])
def test_evidence_backed_correction_without_a_code_change_can_be_reviewed(world, action):
    spec = pr_spec(world, "repair-no-change", 7)
    finding = "[P1] a.txt:1 — alleged defect"
    Reviewer.replies = [deny(finding), allow]
    Worker.replies = ["```disposition\n" + json.dumps([
        {"finding": finding, "action": action, "evidence": "Ran the reported trigger; output is correct"}]) + "\n```"]
    complete = looped(spec["id"])
    assert complete["state"] == "머지 가능" and len(complete["rounds"]) == 2
    assert complete["rounds"][0]["disposition"][0]["action"] == action
    assert complete["rounds"][1]["head"] == spec["pr"]["head"] == world.hub.head(7)
    assert len(Worker.made[0].heard) == 1


def test_the_gate_failing_twice_in_a_row_stops(world):
    pr_spec(world, "fix-c", 7)
    Reviewer.replies = [deny("[P1] a.txt:1 — 틀렸다")]
    Worker.replies = [fixed(("[P1] a.txt:1 — 틀렸다", "fixed"), broken=True), "못 고쳤다"]
    spec = looped("fix-c")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == "게이트"
    assert world.hub.head(7) != git(Path(spec["worktree"]), "rev-parse", "HEAD"), "통과하지 못한 것은 올리지 않는다"


def test_review_stop_is_logged_with_round_and_task_identity(world):
    from common import errorlog

    spec = pr_spec(world, "logged-stop", 7)
    Reviewer.replies = [deny("[P1] a.txt:1 — failed repair")]
    Worker.replies = ["", ""]
    stopped = looped(spec["id"])
    assert stopped["state"] == "멈춤"
    rows = [json.loads(line) for line in errorlog.FILE.read_text(encoding="utf-8").splitlines()]
    row = next(r for r in rows if r["source"] == "review-stop")
    assert (row["repo"], row["spec"], row["pr"], row["round"]) == ("proj", spec["id"], 7, 1)
    assert row["reason"] == stopped["stopped"]["reason"]
    assert row["error"] == stopped["stopped"]["detail"]


@pytest.mark.parametrize("foreground", [False, True], ids=["collected", "foreground-notifications"])
def test_task_completion_advances_successive_review_rounds_automatically(world, monkeypatch, foreground):
    spec = pr_spec(world, "collected-correction", 7, cell={"model": "opus", "effort": "high"})
    finding = "[P1] a.txt:1 — repair with collected background checks"
    corrections = 3 if foreground else 1
    Reviewer.replies = [deny(finding)] * corrections + [allow]
    fixture = '''import json, subprocess, sys
from pathlib import Path
say = lambda m: print(json.dumps(m), flush=True)
foreground = sys.argv[2] == "True"
for turn in range(int(sys.argv[3])):
    sys.stdin.readline()
    for i in range(3 if foreground else 47):
        task = f"{turn}-{i}"
        say({"type": "system", "subtype": "task_started", "task_id": task,
             **({"task_type": "local_bash", "is_backgrounded": False} if foreground else {})})
        if foreground:
            say({"type": "system", "subtype": "task_notification", "task_id": task, "status": "completed"})
        else:
            say({"type": "system", "subtype": "task_updated", "task_id": task, "patch": {"status": "completed"}})
    Path("repair.txt").write_text(f"Fixed {turn}", encoding="utf-8")
    subprocess.run(["git", "add", "repair.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "Repair"], check=True, capture_output=True)
    answer = "```disposition\\n" + json.dumps([{"finding": sys.argv[1], "action": "fixed", "evidence": "All checks collected"}]) + "\\n```"
    say({"type": "result", "origin": {"kind": "human"}, "result": answer, "session_id": "fixture-cli"})
sys.stdin.read()
'''
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        if command[0] == "claude":
            command = [sys.executable, "-X", "utf8", "-c", fixture, finding, str(foreground), str(corrections)]
        return real_popen(command, **kwargs)

    monkeypatch.setattr(work, "ChatSession", chat_session.ChatSession)
    monkeypatch.setattr(chat_session, "cli_command", lambda name: [name])
    monkeypatch.setattr(chat_session.subprocess, "Popen", spawn)
    monkeypatch.setattr(chat_session, "TURN_TIMEOUT", 2)
    complete = looped(spec["id"], seconds=30 * (corrections + 1))
    assert complete["state"] == "머지 가능" and len(complete["rounds"]) == corrections + 1
    assert len({r["head"] for r in complete["rounds"]}) == corrections + 1
    assert all(r.get("disposition") for r in complete["rounds"][:-1])
    assert complete["rounds"][-1]["head"] == world.hub.head(7)


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
    assert "## `gh pr diff 7`" in loop.instruction(spec, path, 1, head, "main", True)
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
    # A read that meets a save on Windows is `None`: read again.
    waited(lambda: sorted(str((specs.load("proj", f"seat-{i}") or {}).get("state")) for i in range(4))
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
    git(world.origin, "branch", "dev", "main")
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
    assert loop.recover() == [("proj", "fix-i")]
    assert specs.load("proj", "fix-i")["stopped"] == {"reason": "서버 재시작", "detail": ""}
    assert specs.load("proj", "fix-j")["state"] == "머지 가능"


@pytest.mark.parametrize("interrupted", ["running", "restart-stop", "correction"])
def test_server_start_resumes_reviews_without_another_button_click(world, interrupted):
    spec = pr_spec(world, "restart-automatic", 7)
    finding = "[P1] a.txt:1 — pending repair at restart"
    if interrupted == "correction":
        Reviewer.replies, Worker.replies = [deny(finding)], ["", ""]
        pending = looped(spec["id"])
        assert pending["state"] == "멈춤" and len(pending["rounds"]) == 1
        specs.update("proj", spec["id"], state="고치는 중 R1", stopped=None)
        Worker.replies = [fixed((finding, "fixed"))]
    elif interrupted == "restart-stop":
        specs.update("proj", spec["id"], state="멈춤", stopped={"reason": loop.Why.RESTART.value, "detail": ""})
    else:
        specs.update("proj", spec["id"], state="리뷰 R1")
    for number, reason in ((8, loop.Why.PERSON), (9, loop.Why.GATE)):
        paused = pr_spec(world, f"paused-{number}", number)
        specs.update("proj", paused["id"], state="멈춤", stopped={"reason": reason.value, "detail": "preserve"})
    Reviewer.replies = [allow]

    async def exercise():
        with patch.object(loop, "poll", lambda *_: None), \
             patch.object(main_app.planning, "recover", lambda: None), patch.object(main_app.survey, "recover", lambda: None):
            async with main_app.lifespan(main_app.app):
                waited(lambda: not loop._loops)
                complete = specs.load("proj", spec["id"])
                assert complete["state"] == "머지 가능"
                if interrupted == "correction":
                    assert len(complete["rounds"]) == 2
                    assert complete["rounds"][1]["head"] != complete["rounds"][0]["head"]
                    assert "round 1" in Worker.made[-1].heard[-1]
                for number in (8, 9):
                    assert specs.load("proj", f"paused-{number}")["stopped"]["detail"] == "preserve"
                    assert not (loop.folder("proj", number) / "round-1.md").exists()

    asyncio.run(exercise())


def test_automatic_dispatch_preserves_a_new_user_stop_and_a_start_failure_can_be_retried(world):
    spec = pr_spec(world, "dispatch-recovery", 7)
    specs.update("proj", spec["id"], state="리뷰 R1")
    restart = loop.recover()
    specs.update("proj", spec["id"], state="멈춤", stopped={"reason": loop.Why.PERSON.value, "detail": "new user stop"})
    for repo, sid in restart:
        loop.kick(repo, sid, automatic=True)
    assert not loop._loops and not Reviewer.made
    assert specs.load("proj", spec["id"])["stopped"]["reason"] == loop.Why.PERSON.value
    with patch.object(loop.threading, "Thread", side_effect=RuntimeError("Cannot start thread")), \
         pytest.raises(RuntimeError, match="Cannot start thread"):
        loop.kick("proj", spec["id"])
    assert not loop._loops
    assert "Cannot start thread" in specs.load("proj", spec["id"])["stopped"]["detail"]
    Reviewer.replies = [allow]
    assert looped(spec["id"])["state"] == "머지 가능"


def test_poller_reattaches_an_active_loop_without_a_driver_and_stops_on_shutdown(world):
    spec = pr_spec(world, "orphaned-review", 7)
    specs.update("proj", spec["id"], state="리뷰 R1")
    Reviewer.replies = [allow]
    halt = threading.Event()
    with patch.object(loop, "POLL", .02):
        thread = threading.Thread(target=loop.poll, args=(halt,), daemon=True)
        thread.start()
        try:
            waited(lambda: specs.load("proj", spec["id"])["state"] == "머지 가능")
            assert len(Reviewer.made) == 1 and len(Reviewer.made[0].heard) == 1
        finally:
            halt.set()
            thread.join(2)
        assert not thread.is_alive()
        waited(lambda: not loop._loops)


@pytest.mark.parametrize("producer", ["planning", "work"])
def test_shutdown_keeps_late_review_handoffs_pending_for_the_next_owner(world, producer):
    spec = pr_spec(world, "late-shutdown-review", 7)
    entered, release = threading.Event(), threading.Event()

    def held_step(_loop):
        entered.set()
        assert release.wait(120)
        return False

    async def shutdown():
        async with main_app.lifespan(main_app.app):
            pass

    try:
        with patch.object(loop, "step", held_step), patch.object(loop, "poll", lambda *_: None), \
             patch.object(main_app.planning, "recover", lambda: None), \
             patch.object(main_app.survey, "recover", lambda: None), \
             patch.object(getattr(main_app, producer), "close_all", lambda: loop.kick("proj", spec["id"])):
            asyncio.run(shutdown())
        assert not loop._loops and not entered.is_set(), "A shutdown handoff started a late review driver"
        assert specs.load("proj", spec["id"])["state"] == "리뷰 대기"
    finally:
        release.set()
        loop.close_all()
    Reviewer.replies = [allow]
    with patch.object(loop, "poll", lambda *_: None), \
         patch.object(main_app.planning, "recover", lambda: None), patch.object(main_app.survey, "recover", lambda: None):
        async def restarted():
            async with main_app.lifespan(main_app.app):
                waited(lambda: not loop._loops)
                assert specs.load("proj", spec["id"])["state"] == "머지 가능"
        asyncio.run(restarted())


def test_shutdown_drains_an_accepted_work_callback_before_releasing_ownership(world):
    from main.runtime import server_owner

    spec = pr_spec(world, "accepted-shutdown-review", 7)
    entered, requested, closing, release, finished = (threading.Event() for _ in range(5))
    errors, runs = [], []

    def callback():
        entered.set()
        assert release.wait(120)
        loop.kick("proj", spec["id"])

    original_close = work.close_all

    def close():
        closing.set()
        original_close()

    async def server():
        async with main_app.lifespan(main_app.app):
            path = Path(spec["worktree"])
            runs.append(work.begin(path, work.session(path, "codex:test", "high"), "accepted", lambda: None))
            assert requested.wait(120)

    def own():
        try:
            asyncio.run(server())
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    Worker.replies = ["accepted"]
    with patch.object(specs, "check", lambda *_: callback), patch.object(loop, "poll", lambda *_: None), \
         patch.object(main_app.planning, "recover", lambda: None), patch.object(main_app.survey, "recover", lambda: None), \
         patch.object(work, "close_all", close):
        owner = threading.Thread(target=own, daemon=True)
        owner.start()
        try:
            assert entered.wait(120) and runs[0].done
            requested.set()
            assert closing.wait(120)
            assert not finished.wait(.2), "Ownership was released before the accepted callback finished"
            with pytest.raises(RuntimeError, match="이미 실행 중"):
                with server_owner(specs.SPECS.parent):
                    pytest.fail("A replacement owner overlapped the accepted callback")
        finally:
            requested.set()
            release.set()
            owner.join(15)
    assert not owner.is_alive() and not errors and not loop._loops and not work._turns
    assert specs.load("proj", spec["id"])["state"] == "리뷰 대기"
    with server_owner(specs.SPECS.parent):
        pass


def test_a_second_server_cannot_stop_another_servers_live_review(world):
    entered, release = threading.Event(), threading.Event()

    def reviewing(first, text):
        entered.set()
        assert release.wait(120), "The fixture did not release the reviewer"
        return allow(first, text)

    child = '''import asyncio, sys
from pathlib import Path
from main import app, specs, loop, planning, survey, architecture, query
specs.SPECS = Path(sys.argv[1])
query.LOGS = specs.SPECS.parent / "chat"
loop.poll = lambda *_: None
planning.recover = survey.recover = lambda: None
async def run():
    async with app.lifespan(app.app):
        print("second server started")
asyncio.run(run())
'''

    async def exercise():
        with patch.object(loop, "poll", lambda *_: None), \
             patch.object(main_app.planning, "recover", lambda: None), patch.object(main_app.survey, "recover", lambda: None):
            async with main_app.lifespan(main_app.app):
                spec = pr_spec(world, "server-owned", 7)
                Reviewer.replies = [reviewing]
                loop.kick("proj", spec["id"])
                assert entered.wait(120)
                before = specs.load("proj", spec["id"])
                try:
                    duplicate = subprocess.run([sys.executable, "-X", "utf8", "-c", child, str(specs.SPECS)],
                                               cwd=Path(__file__).parent, capture_output=True, text=True,
                                               encoding="utf-8", timeout=20)
                    assert duplicate.returncode != 0, duplicate.stdout + duplicate.stderr
                    assert "second server started" not in duplicate.stdout
                    assert specs.load("proj", spec["id"]) == before
                finally:
                    release.set()
                    waited(lambda: not loop._loops)
                assert specs.load("proj", spec["id"])["state"] == "머지 가능"

    asyncio.run(exercise())


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
    assert web.get("/api/prs").json()["rows"][0]["pickable"] is True, "An allowed PR can request another review round"


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
    assert loop._cells == {} and not (world.tmp / "review/proj/7").exists()
    told = [r["text"] for r in chat.recall("next") if r["role"] == "result"]
    assert told[-1] == "PR #7 머지됨 — 라운드 1, 남은 P2 1"


def test_legacy_cleanup_preserves_a_local_commit_added_after_review(world):
    spec = looped(pr_spec(world, "late-local", 7)["id"])
    path = Path(spec["worktree"])
    commit = world.hub.squash(7)
    git(path, "commit", "--allow-empty", "-m", "local after review")
    newer = git(path, "rev-parse", "HEAD")
    loop.finish(world.repo, spec, "main", commit, "Merged fixture")
    assert not path.exists()
    assert git(world.repo, "rev-parse", "late-local") == newer
    assert not specs.load("proj", "late-local")["cleanup_complete"]


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


def base_onto_first(w, name: str, n: int) -> tuple[str, dict]:
    """A two-commit pull request made mergeable; then the base fast-forwards
    onto its first commit, which moves the merge base. Only a fetch shows it."""

    first = pr_spec(w, name, n)["pr"]["head"]
    path = Path(specs.load("proj", name)["worktree"])
    commit(path, "second.txt")
    git(path, "push", "-q", "origin", name)
    spec = looped(name)
    assert spec["state"] == "머지 가능"
    git(w.hub.elsewhere(), "push", "-q", "origin", f"{first}:refs/heads/main")
    return first, spec


def conflicting_base(w, filename="collision.txt"):
    path = w.hub.elsewhere()
    git(path, "checkout", "-q", "-B", "main", "origin/main")
    head = commit(path, filename, "upstream behavior\n")
    git(path, "push", "-q", "origin", "main")
    return head


def integrate_conflict(path, halt):
    result = subprocess.run(["git", "merge", "--no-commit", "origin/main"], cwd=path, capture_output=True)
    assert result.returncode == 1
    (path / "collision.txt").write_text("upstream behavior\ntask behavior\n", encoding="utf-8")
    git(path, "add", "collision.txt")
    git(path, "commit", "-qm", "Integrate both behaviors")
    return "Integrated the current base; ready for new checks and review."


def test_conflicting_base_is_repaired_before_review_and_preserves_both_sides(world):
    spec = pr_spec(world, "base-conflict", 7, file="collision.txt")
    old_base = git(Path(spec["worktree"]), "merge-base", "HEAD", "origin/main")
    tip = conflicting_base(world)
    preview = loop.merge_preview(Path(spec["worktree"]), spec["pr"]["head"], "main")
    assert not preview["ok"] and preview["base_oid"] == tip
    assert git(Path(spec["worktree"]), "merge-base", "HEAD", "origin/main") == old_base
    assert not git(Path(spec["worktree"]), "status", "--porcelain")
    assert git(Path(spec["worktree"]), "rev-parse", "HEAD") == spec["pr"]["head"]
    Worker.replies = [integrate_conflict]
    fresh = looped(spec["id"])
    assert fresh["state"] == "머지 가능", fresh
    assert len(fresh["rounds"]) == 1 and fresh["rounds"][0]["head"] != spec["pr"]["head"]
    assert len(Worker.made) == 1 and "git merge-tree" in Worker.made[0].heard[0]
    assert fresh["validation"]["final"]["head"] == fresh["rounds"][0]["head"]
    assert (Path(spec["worktree"]) / "collision.txt").read_text(encoding="utf-8") == "upstream behavior\ntask behavior\n"


def test_merge_rechecks_conflict_when_base_moves_without_changing_merge_base(world):
    spec = looped(pr_spec(world, "approved-conflict", 7, file="collision.txt")["id"])
    conflicting_base(world)
    Worker.replies = [integrate_conflict]
    response = client().post(f"/api/specs/{spec['id']}/merge", json={"head": spec["pr"]["head"]})
    assert response.status_code == 409 and "병합 충돌" in response.json()["detail"]
    assert not any(args[:3] == ["gh", "pr", "merge"] for args in world.hub.calls)
    waited(lambda: ("proj", spec["id"]) not in loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "머지 가능" and len(fresh["rounds"]) == 2
    assert fresh["rounds"][0]["head"] != fresh["rounds"][1]["head"]


def test_failed_merge_repair_stops_once_and_persists_across_resume(world):
    spec = pr_spec(world, "failed-integration", 7, file="collision.txt")
    conflicting_base(world)
    Worker.replies = [lambda path, halt: "Could not resolve the conflict."]
    fresh = looped(spec["id"])
    assert fresh["state"] == "멈춤" and "포함되지 않았다" in fresh["stopped"]["detail"]
    assert not fresh["rounds"] and not fresh.get("auto_merge_pending")
    assert len(Worker.made[0].heard) == 1
    resumed = looped(spec["id"])
    assert resumed["state"] == "멈춤" and "충돌이 남아 있다" in resumed["stopped"]["detail"]
    assert len(Worker.made[0].heard) == 1


def test_external_merge_conflict_never_dispatches_local_implementation(world):
    spec = pr_spec(world, "external-conflict", 7, file="collision.txt", implementation_environment="external")
    conflicting_base(world)
    fresh = looped(spec["id"])
    assert fresh["state"] == "멈춤" and fresh["stopped"]["reason"] == loop.Why.EXTERNAL.value
    assert not Worker.made and not Reviewer.made


@pytest.mark.parametrize("manual", [False, True])
def test_nonconflict_merge_failure_does_not_keep_automatic_retry_pending(world, manual):
    spec = looped(pr_spec(world, "refused-merge", 7)["id"])
    real = world.hub
    def refuse(args, cwd, timeout=60):
        if args[:3] == ["gh", "pr", "merge"]:
            return subprocess.CompletedProcess(args, 1, "", "Repository policy refused merge")
        return real(args, cwd, timeout)
    with patch.object(specs, "sh", refuse):
        if manual:
            with pytest.raises(Exception, match="머지하지 못했다"):
                loop.merge_spec(world.repo, spec, loop.Merge(head=spec["pr"]["head"]))
        else:
            specs.update("proj", spec["id"], merge_request={"head": spec["pr"]["head"], "base": "main", "rev": 1, "pr": 7})
            loop.requested_merge(world.repo, specs.load("proj", spec["id"]))
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "머지 가능" and not fresh["merge_request"]
    if not manual:
        assert "요청한 머지 중단" in fresh["fault"]


def test_conflict_created_during_final_gate_requires_new_head_and_review(world):
    spec = pr_spec(world, "gate-base-race", 7, file="collision.txt")
    original = specs.judge
    advanced = []
    def gate(path, cmds, halt, noted=lambda text: None):
        if not advanced:
            advanced.append(conflicting_base(world))
        return original(path, cmds, halt, noted)
    Worker.replies = [integrate_conflict]
    with patch.object(specs, "judge", gate):
        fresh = looped(spec["id"])
    assert fresh["state"] == "머지 가능" and len(fresh["rounds"]) == 2
    assert fresh["rounds"][0]["head"] != fresh["rounds"][1]["head"]
    assert fresh["validation"]["final"]["head"] == fresh["rounds"][1]["head"]


def test_conflict_created_between_preview_and_github_merge_returns_to_repair(world):
    spec = looped(pr_spec(world, "github-base-race", 7, file="collision.txt")["id"])
    original = world.hub
    attempts = []
    def race(args, cwd, timeout=60):
        if args[:3] == ["gh", "pr", "merge"]:
            attempts.append(args)
            conflicting_base(world)
            return subprocess.CompletedProcess(args, 1, "", "Merge commit cannot be cleanly created")
        return original(args, cwd, timeout)
    Worker.replies = [integrate_conflict]
    with patch.object(specs, "sh", race):
        response = client().post(f"/api/specs/{spec['id']}/merge", json={"head": spec["pr"]["head"]})
        assert response.status_code == 409 and "머지 직전" in response.json()["detail"]
        waited(lambda: ("proj", spec["id"]) not in loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert len(attempts) == 1 and fresh["state"] == "머지 가능" and len(fresh["rounds"]) == 2


def test_merge_preview_failure_cannot_be_reported_as_a_clean_merge(world):
    spec = pr_spec(world, "preview-error", 7)
    original = world.hub
    def fail(args, cwd, timeout=60):
        if args[:3] == ["git", "merge-tree", "--write-tree"]:
            return subprocess.CompletedProcess(args, 2, "", "Fixture unsupported merge-tree")
        return original(args, cwd, timeout)
    with patch.object(specs, "sh", fail), pytest.raises(ValueError, match="검사하지 못했다"):
        loop.merge_preview(Path(spec["worktree"]), spec["pr"]["head"], "main")


def test_merge_reads_the_base_as_it_stands_now_not_as_last_fetched(world):
    first, spec = base_onto_first(world, "fix-mb", 7)
    answer = client().post("/api/specs/fix-mb/merge", json={"head": spec["rounds"][0]["head"]})
    assert answer.status_code == 409 and "base" in answer.json()["detail"]
    assert not any(c[1:3] == ["pr", "merge"] for c in world.hub.calls)
    waited(lambda: ("proj", "fix-mb") not in loop._loops)
    assert specs.load("proj", "fix-mb")["validation"]["final"]["base_oid"] == first, "the final gate ran again"


def test_a_standing_final_result_is_reused_only_for_the_base_as_it_stands_now(world):
    first, _ = base_onto_first(world, "fix-mc", 7)
    with judged(calls := []):
        spec = looped("fix-mc")   # the screen asks again
    assert calls == [[FINAL]] and spec["validation"]["final"]["base_oid"] == first


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


@pytest.mark.parametrize("queue_read", [False, True])
def test_failed_merge_observation_is_logged_without_advancing_cleanup(tmp_path, queue_read):
    from common import errorlog

    repo = tmp_path / "fixture"
    spec = {"id": "status-error", "repo": repo.name, "state": "머지 대기",
            "pr": {"number": 70, "base": "main", "head": "a" * 40}, "rounds": []}

    def observation(*_args):
        if queue_read:
            return {"state": "OPEN"}
        raise RuntimeError("Fixture status failure")

    with patch.object(specs, "load", return_value=spec), patch.object(loop, "gh_json", observation), \
         patch.object(loop, "in_queue", side_effect=RuntimeError("Fixture queue failure")), \
         patch.object(loop, "finish") as finish:
        loop.landed(repo, spec)
    finish.assert_not_called()
    assert spec["state"] == "머지 대기"
    rows = [json.loads(line) for line in errorlog.FILE.read_text(encoding="utf-8").splitlines()]
    row = next(row for row in rows if row["source"] == "merge-status")
    assert (row["repo"], row["spec"], row["pr"]) == ("fixture", "status-error", 70)
    assert row["type"] == "RuntimeError" and "Fixture" in row["error"]


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


# -- the gate contract: targeted rounds, one final gate on the allowed head ------------------------


def judged(calls: list):
    """`specs.judge` as it is, noting the commands of every run."""

    real = specs.judge

    def run(path, cmds, halt, noted=lambda text: None):
        calls.append(list(cmds))
        return real(path, cmds, halt, noted)
    return patch.object(specs, "judge", run)


def allowed_spec(w, name: str, n: int, **validation) -> dict:
    """A spec whose one round allowed its head, in `머지 가능`."""

    spec = pr_spec(w, name, n)
    specs.update("proj", name, state="머지 가능", rounds=[{"n": 1, "head": spec["pr"]["head"], "base": "main",
                                                         "verdict": "allow", "findings": {"P0": 0, "P1": 0, "P2": 0}}])
    if validation:
        specs.validate("proj", name, **validation)
    return specs.load("proj", name)


def final_of(w, spec: dict, **extra) -> dict:
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    cmd = specs.required(w.repo, spec)
    return {"head": head, "base_oid": specs.merge_base(path, "main", head), "command": cmd,
            "environment_digest": specs.digest(w.repo, path, cmd), "ok": True, "code": 0, "reason": "",
            "finished_at": 1.0, **extra}


def round_of(spec: dict) -> dict:
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    return {"head": head, "base_oid": specs.merge_base(path, "main", head), "commands": [FINAL],
            "selection": "full", "ok": True, "finished_at": 1.0}


def test_a_targeted_pass_cannot_merge_and_the_loop_runs_only_the_final_gate(world):
    """Every other reason `specs.proven` refuses is its own unit test
    (`test_specs.py`); `merge` asks it, and each refusal takes this path."""

    web = client()
    spec = allowed_spec(world, "fix-u", 7)
    specs.validate("proj", "fix-u", round=round_of(spec))
    with judged(calls := []):
        answer = web.post("/api/specs/fix-u/merge", json={"head": spec["pr"]["head"]})
        assert answer.status_code == 409 and "최종 게이트" in answer.json()["detail"]
        assert not any(c[1:3] == ["pr", "merge"] for c in world.hub.calls)
        waited(lambda: ("proj", "fix-u") not in loop._loops)
    assert calls == [[FINAL]], "the round result is reused; only the final gate runs"
    spec = specs.load("proj", "fix-u")
    assert spec["state"] == "머지 가능" and spec["validation"]["final"]["ok"] and len(spec["rounds"]) == 1
    assert not any(r.heard for r in Reviewer.made), "the review was not asked again"
    web.post("/api/specs/fix-u/merge", json={"head": spec["pr"]["head"]}).raise_for_status()
    assert specs.load("proj", "fix-u")["state"] == "머지됨"


def test_the_screen_is_told_the_server_s_proof_not_the_saved_pass(world):
    spec = allowed_spec(world, "fix-v", 7)
    specs.validate("proj", "fix-v", final=final_of(world, spec))

    def shown() -> str | None:
        return next(s for s in client().get("/api/specs").json()["specs"] if s["id"] == "fix-v")["unproven"]

    assert shown() == ""
    adapter = world.repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(GATE, GATE + " && git --version"), encoding="utf-8")
    assert "환경" in shown(), "a changed gate_cmd leaves the saved pass standing for nothing"
    specs.update("proj", "fix-v", state="리뷰 R2")
    assert shown() is None


def test_a_worktree_gone_from_disk_is_unproven_and_the_rest_still_list(world):
    healthy, gone = allowed_spec(world, "fix-w", 7), allowed_spec(world, "fix-x", 8)
    for spec in (healthy, gone):
        specs.validate("proj", spec["id"], final=final_of(world, spec))
    Path(gone["worktree"]).rename(world.tmp / "moved-away")
    listed = client().get("/api/specs")
    assert listed.status_code == 200, listed.text
    shown = {s["id"]: s["unproven"] for s in listed.json()["specs"]}
    assert shown["fix-w"] == "" and "작업트리" in shown["fix-x"]


def test_a_restart_during_the_final_gate_stays_blocked_and_resume_reruns_only_it(world):
    spec = allowed_spec(world, "fix-y", 7)
    specs.update("proj", "fix-y", state="리뷰 R1")
    specs.validate("proj", "fix-y", round=round_of(spec), phase="final_running",
                   final=final_of(world, spec, ok=False, code=None, reason="끝나지 않았다", finished_at=None))
    loop.recover()
    spec = specs.load("proj", "fix-y")
    assert spec["stopped"]["reason"] == "서버 재시작" and spec["validation"]["phase"] is None
    assert not spec["validation"]["final"]["ok"]
    assert client().post("/api/specs/fix-y/merge", json={"head": spec["pr"]["head"]}).status_code == 409
    with judged(calls := []):
        client().post("/api/specs/fix-y/resume", json={}).raise_for_status()
        waited(lambda: ("proj", "fix-y") not in loop._loops)
    assert calls == [[FINAL]], "the unchanged round result is reused; the final gate runs again"
    spec = specs.load("proj", "fix-y")
    assert spec["state"] == "머지 가능" and spec["validation"]["final"]["finished_at"]


def mapped(w, *globs: str) -> None:
    """Register `git --version` as the check for `globs` in the adapter."""

    adapter = w.repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8") + '\n[checks.text]\ncmd = "git --version"\npaths = '
                       + json.dumps(list(globs)) + "\n", encoding="utf-8")


def test_a_mapped_round_then_the_full_gate_once_and_the_same_identity_reuses_both(world):
    mapped(world, "*.txt")
    pr_spec(world, "fix-z", 7, gate=None)
    with judged(calls := []):
        spec = looped("fix-z")
        assert calls == [["git --version"], [FINAL]] and spec["state"] == "머지 가능"
        v = spec["validation"]
        assert v["round"]["selection"] == "mapped" and v["final"]["ok"] and v["final"]["head"] == spec["pr"]["head"]
        assert "the checks this change maps to" in order(world, 7, 1)
        # The screen asks again: nothing changed, so nothing runs.
        spec = looped("fix-z")
    assert calls == [["git --version"], [FINAL]] and spec["state"] == "머지 가능" and len(spec["rounds"]) == 1


def test_a_failed_final_gate_goes_to_repair_and_a_new_review(world):
    """The round's mapped check passes; the full gate on the allowed head
    does not. The repair's commit gets its own round checks and review."""

    mapped(world, "*")
    path = Path(pr_spec(world, "fix-f2", 7, gate=None)["worktree"])
    broken = commit(path, "broken")
    git(path, "push", "-q", "origin", "fix-f2")
    Reviewer.replies = [allow, allow]
    Worker.replies = [unbroken]
    spec = looped("fix-f2")
    assert "python gate.py" in Worker.made[-1].heard[0] and "failed" in Worker.made[-1].heard[0]
    assert spec["state"] == "머지 가능" and spec["rounds"][0]["head"] == broken and len(spec["rounds"]) == 2
    assert spec["validation"]["final"]["ok"] and spec["validation"]["final"]["head"] == world.hub.head(7) != broken


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
    assert spec["state"] == "머지됨" and not spec["cleanup_complete"]
    assert Path(spec["worktree"]).exists(), "No refs are deleted before the accepted base is synchronized"
    git(world.repo, "push", "origin", "main:refs/heads/dev")
    merged = world.hub.squash(8)
    git(world.repo, "fetch", "origin")
    git(world.repo, "switch", "--track", "-c", "dev", "origin/dev")
    loop.finish(world.repo, spec, "dev", merged, "")
    spec = specs.load("proj", "fix-p")
    assert spec["cleanup_complete"] and not Path(spec["worktree"]).exists()
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


def test_codex_review_migrates_old_tools_but_resumes_the_readonly_profile(world):
    spec = pr_spec(world, "read-profile", 7)
    kept = loop.folder("proj", 7) / "session.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    with patch.object(loop, "review_model", return_value="codex:test"):
        kept.write_text(json.dumps({"provider": "codex", "session_id": "old-shell-thread"}), encoding="utf-8")
        assert loop.cell(spec, Path(spec["worktree"])).session_id is None
        loop.close_cell("proj", 7)
        kept.write_text(json.dumps({"provider": "codex", "session_id": "safe-thread",
                                    "tools_profile": loop.READ_PROFILE}), encoding="utf-8")
        assert loop.cell(spec, Path(spec["worktree"])).session_id == "safe-thread"
        cloud_spec = {**spec, "implementation_environment": "claude-cloud"}
        cloud = loop.cell(cloud_spec, Path(spec["worktree"]))
        assert cloud.session_id is None and cloud.verification and cloud.tools == loop.CLOUD_TOOLS
        loop.close_cell("proj", 7)
        kept.write_text(json.dumps({"provider": "codex", "session_id": "cloud-thread",
                                    "tools_profile": loop.CLOUD_PROFILE}), encoding="utf-8")
        assert loop.cell(cloud_spec, Path(spec["worktree"])).session_id == "cloud-thread"
        ordinary = loop.cell(spec, Path(spec["worktree"]))
        assert ordinary.session_id is None and ordinary.verification is None and ordinary.tools == loop.REVIEW_TOOLS


@pytest.mark.parametrize("model", ["codex:test", "sonnet"])
@pytest.mark.parametrize("field", ["environment_id", "test_scope", "browser_tool"])
def test_cloud_review_refreshes_changed_environment_but_keeps_the_conversation(world, model, field):
    spec = pr_spec(world, "review-context", 7, implementation_environment="claude-cloud")
    context = {"environment_id": "test-api", "test_scope": "fixture-a", "browser_tool": "browser-a"}
    marker = {"environment_id": "WIKI_VERIFICATION_ENVIRONMENT", "test_scope": "WIKI_VERIFICATION_SCOPE",
              "browser_tool": "WIKI_VERIFICATION_BROWSER"}[field]
    with patch.object(loop, "ChatSession", chat_session.ChatSession), \
         patch.object(loop, "review_model", return_value=model), \
         patch.object(loop.verification, "local", side_effect=lambda repo: dict(context)):
        first = loop.cell(spec, Path(spec["worktree"]))
        assert loop.cell(spec, Path(spec["worktree"])) is first
        saved = loop.folder("proj", 7) / "session.json"
        saved.write_text(json.dumps({"provider": "codex" if first.is_codex else "claude",
                                     "session_id": "same-review-thread", "tools_profile": loop.CLOUD_PROFILE}),
                         encoding="utf-8")
        context[field] = "updated-context"
        with patch.object(first, "close", wraps=first.close) as closed:
            fresh = loop.cell(spec, Path(spec["worktree"]))
            closed.assert_called_once()
        assert fresh is not first and fresh._env[marker] == "updated-context"
        assert fresh.session_id == "same-review-thread" and fresh.verification == first.verification
        assert loop.cell(spec, Path(spec["worktree"])) is fresh


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
