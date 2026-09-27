"""The task spec: its checks, the `next` focus's blocks, `[시작]`, the server's
own completion check, and the pull request with the result coming back.

`gh` and `git push` are stood in for through `specs.sh`. The write session is
a stand-in object, except where the CLI's own arguments are the evidence.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

import harvest
from agent import chat_session
from agent.chat_session import Event
from main import channels as chat_channels
from main import query as chat
from main import loop, specs, work
from test_main import _repo, client, no_machine_settings, parse, settled, until  # noqa: F401 — the fixture is autouse

PASS = "git --version"
FAIL = "git definitely-not-a-command"


def _adapter(repo: Path, gate: str | None) -> None:
    (repo / ".wiki").mkdir(exist_ok=True)
    slots = f'gate_cmd = "{gate}"\n' if gate else 'review_dir = "x"\n'
    (repo / ".wiki/adapter.toml").write_text(f"[slots]\n{slots}", encoding="utf-8")


KICKED: list = []


@pytest.fixture
def repo(tmp_path):
    """A project named `proj` with a gate that passes, selected. The review
    loop is not started here — `test_loop.py` drives it — only noted."""

    repo = _repo(tmp_path)
    _adapter(repo, PASS)
    KICKED.clear()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), \
         patch.object(loop, "kick", side_effect=lambda name, sid: KICKED.append((sid, specs.load(name, sid)))):
        yield repo


def spec_block(**extra) -> dict:
    return {"slug": "Fix Login!", "goal": "로그인 뒤 원래 페이지로 돌아간다", "out": ["세션 만료"],
            "done": ["돌아오는 테스트가 있다"], "grounds": {"pages": ["craft/x"], "files": ["a.txt:1", "nope.py"]},
            "decisions": [{"what": "서버에서 돌린다", "why": "화면은 기록을 모른다", "rejected": "history"}], **extra}


def made(repo: Path, block: dict, session: str = "s1") -> list[dict]:
    return specs.answered(repo, [{"name": "spec", "value": block}], {"focus": "next", "turn": 1.0, "session": session})


class Remote:
    """GitHub and the remote, as `specs.sh` sees them. Everything else runs."""

    def __init__(self, merged=False):
        self.calls, self.body, self.merged, self.real = [], "", merged, specs.sh
        self.on_push = lambda: None   # what else happens while a push runs

    def __call__(self, args, cwd, timeout=60):
        self.calls.append(args)
        ok = lambda out="": subprocess.CompletedProcess(args, 0, out, "")  # noqa: E731
        if args[:2] == ["git", "push"]:
            self.on_push()
            return ok()
        if args[:3] == ["gh", "repo", "view"]:
            return ok("main\n")
        if args[:3] == ["gh", "pr", "create"]:
            self.body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
            return ok("https://github.com/o/proj/pull/7\n")
        if args[:3] == ["gh", "pr", "view"]:
            return ok(json.dumps({"state": "MERGED" if self.merged else "OPEN", "mergedAt": None}))
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 1, "", "gh 가 로그인되어 있지 않다\n")
        return self.real(args, cwd, timeout)

    def pushes(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ["git", "push"])

    def created(self) -> bool:
        return any(c[:3] == ["gh", "pr", "create"] for c in self.calls)


def report(*passes: bool) -> str:
    items = [{"item": f"항목 {i}", "pass": p, "evidence": f"ran {i} · ok"} for i, p in enumerate(passes)]
    return "다 했다.\n\n```done-report\n" + json.dumps(items, ensure_ascii=False) + "\n```"


class Worker:
    """A write session that answers each turn with the next of `replies`. A
    callable reply is called with the worktree and the turn's stop first."""

    replies: list = []
    made: list = []

    def __init__(self, path, model="", effort="", write=False, system="", bypass=False):
        self.path, self.system = Path(path), system
        self.id, self.session_id, self.alive, self.parent_id = uuid.uuid4().hex, None, True, None
        self.is_codex, self.rules, self.heard, self.bypass = False, [], [], bypass
        Worker.made.append(self)

    def say(self, text, halt=None):
        self.heard.append(text)
        reply = Worker.replies.pop(0) if Worker.replies else "알겠다"
        if callable(reply):
            reply = reply(self.path, halt)
        yield Event("done", reply, {"session_id": "cli-1", "error": False}, self.id)

    def reconfigure(self, model, effort):
        pass

    def stop(self, halt):
        pass

    def close(self):
        self.alive = False


@pytest.fixture(autouse=True)
def fresh_workers():
    Worker.replies, Worker.made = [], []
    yield
    work.close_all()


def started(web, sid: str) -> str:
    """`[시작]` and wait for its first turn to end; the worktree's path."""

    answer = web.post(f"/api/specs/{sid}/start", json={})
    assert answer.status_code == 200, answer.text
    path = answer.json()["path"]
    settled(path)
    return path


# -- the checks ----------------------------------------------------------------


def test_the_checks_refuse_an_empty_goal_keep_the_gate_first_and_want_an_adapter(repo, tmp_path):
    assert made(repo, spec_block(goal="  "))[0]["error"] == "`goal` 이 비었다"

    [entry] = made(repo, spec_block(done=["돌아오는 테스트가 있다", PASS]))
    spec = specs.load("proj", entry["id"])
    assert spec["done"] == [PASS, "돌아오는 테스트가 있다"], "게이트는 늘 첫 항목, 한 번만"
    assert spec["state"] == "정리됨" and spec["rev"] == 1
    assert spec["grounds"]["files"] == ["a.txt:1", "nope.py"]
    assert specs.view(repo, spec)["missing"] == ["nope.py"], "없는 경로는 지우지 않고 표시한다"

    _adapter(repo, None)
    assert "연결 먼저" in made(repo, spec_block(slug="other"))[0]["error"]


def test_a_slug_is_tidied_and_a_taken_one_gets_a_number(repo):
    first = made(repo, spec_block())[0]["id"]
    assert first == "fix-login"
    # Another conversation with the same slug: a new card, not an overwrite.
    assert made(repo, spec_block(), session="s2")[0]["id"] == "fix-login-2"
    subprocess.run(["git", "-C", str(repo), "branch", "fix-login-3"], check=True)
    assert made(repo, spec_block(), session="s3")[0]["id"] == "fix-login-4", "브랜치 이름도 겹침이다"
    assert made(repo, spec_block(slug="한글만"))[0]["error"].startswith("`slug`")


def test_the_same_slug_in_the_same_conversation_replaces_the_card(repo):
    sid = made(repo, spec_block())[0]["id"]
    again = made(repo, spec_block(goal="새 목표"))[0]["id"]
    spec = specs.load("proj", sid)
    assert again == sid and spec["rev"] == 2 and spec["goal"] == "새 목표"


def test_a_save_from_a_stale_card_is_refused(repo):
    web = client()
    sid = made(repo, spec_block())[0]["id"]
    body = {"rev": 1, "goal": "고친 목표", "out": [], "done": ["게이트 없이 쓴 줄"]}
    saved = web.put(f"/api/specs/{sid}", json=body).json()
    assert saved["rev"] == 2 and saved["done"] == [PASS, "게이트 없이 쓴 줄"]
    assert web.put(f"/api/specs/{sid}", json=body).status_code == 409
    assert web.put(f"/api/specs/{sid}", json={**body, "rev": 2, "goal": ""}).status_code == 400
    renamed = web.put(f"/api/specs/{sid}", json={**body, "rev": 2, "slug": "Other Name"}).json()
    assert renamed["id"] == "other-name" and specs.load("proj", sid) is None


def test_an_id_from_the_screen_is_a_task_name_never_a_path(repo, tmp_path):
    outside = tmp_path / "specs" / "x.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("{}", encoding="utf-8")
    web = client()
    for sid in ("..%5Cx", "..%2Fx", "X"):
        assert web.post(f"/api/specs/{sid}/drop").status_code == 404
    assert outside.exists()
    (tmp_path / "specs" / "proj").mkdir(exist_ok=True)
    (tmp_path / "specs" / "proj" / "Stray.json").write_text("{}", encoding="utf-8")
    assert web.get("/api/specs").json()["specs"] == []


# -- blocks ----------------------------------------------------------------------


def test_blocks_leave_the_answer_and_a_broken_one_is_an_error():
    text = ("고를 것이 둘이다.\n\n```choices\n{\"question\": \"범위?\", \"options\": [{\"label\": \"좁게\"}]}\n```\n"
            "```spec\n{not json\n```\n```retro-candidates\n그대로\n```\n")
    body, found = specs.blocks(text)
    assert body == "고를 것이 둘이다.\n\n```retro-candidates\n그대로\n```"
    assert found[0] == {"name": "choices", "value": {"question": "범위?", "options": [{"label": "좁게"}]}}
    assert found[1]["name"] == "spec" and found[1]["error"].startswith("JSON 이 아니다")
    broken = specs.answered(Path("."), [{"name": "candidates", "value": [{"why": "x"}]}], {})
    assert broken[0]["error"] == "후보마다 `title` 이 있어야 한다"


def test_the_next_focus_sends_blocks_apart_and_records_them(repo):
    answer = "후보는 하나다.\n\n```spec\n" + json.dumps(spec_block(), ensure_ascii=False) + "\n```"

    class Next:
        def say(self, text, halt=None):
            yield Event("done", answer, {"session_id": "cli-9", "error": False})

    def plain(source, model, effort):
        assert "```" not in source
        yield Event("done", "쉬운 설명")

    with patch.object(chat, "session", return_value=Next()), patch.object(chat, "explain", plain):
        events = parse(client().post("/api/say/next", json={"text": "다음은?"}).text)
    done = next(e for e in events if e["kind"] == "done")
    assert done["text"] == "후보는 하나다."
    assert next(e for e in events if e["kind"] == "blocks")["blocks"] == [{"name": "spec", "id": "fix-login"}]
    assert chat.recall("next")[-1]["blocks"] == [{"name": "spec", "id": "fix-login"}]
    assert specs.load("proj", "fix-login")["source"]["session"] == "cli-9"


def test_asking_for_candidates_sends_the_materials_and_shows_one_line(repo):
    heard = []

    class Next:
        def say(self, text, halt=None):
            heard.append(text)
            yield Event("done", "후보", {"session_id": "cli-9", "error": False})

    with patch.object(chat, "session", return_value=Next()), \
         patch.object(chat, "explain", lambda *a: iter([Event("done", "쉬운")])), \
         patch.object(specs, "sh", Remote()), patch.object(specs, "_warnings", lambda repo: "- 경고 하나"):
        web = client()
        assert web.post("/api/say/wiki", json={"propose": True}).status_code == 400
        web.post("/api/say/next", json={"propose": True})
    assert "## Open pull requests\n\n(could not read: gh 가 로그인되어 있지 않다)" in heard[0]
    assert "- 경고 하나" in heard[0] and "## Review P2 left\n\n(none)" in heard[0]
    row = chat.recall("next")[0]
    assert row["text"] == heard[0] and row["said"] == "(후보 요청)" and row["propose"]


def test_a_result_goes_in_front_of_the_next_thing_said_once(repo):
    heard = []

    class Next:
        def say(self, text, halt=None):
            heard.append(text)
            yield Event("done", "알았다", {"session_id": "cli-9", "error": False})

    specs.told(repo, {"id": "fix-login"}, "PR #7 — 로그인. 완료 조건 2개 통과")
    with patch.object(chat, "session", return_value=Next()), \
         patch.object(chat, "explain", lambda *a: iter([Event("done", "쉬운")])):
        web = client()
        web.post("/api/say/next", json={"text": "첫 말"})
        web.post("/api/say/next", json={"text": "둘째 말"})
    assert heard[0] == "Since your last turn:\n- PR #7 — 로그인. 완료 조건 2개 통과\n\n첫 말"
    assert heard[1] == "둘째 말"
    rows = web.get("/api/log/next").json()
    assert [r["role"] for r in rows] == ["result", "user", "assistant", "user", "assistant"]
    assert rows[1]["said"] == "첫 말"


# -- `[시작]` and the work session ---------------------------------------------------


def test_start_makes_the_worktree_first_and_a_failure_leaves_the_spec_as_it_was(repo):
    web = client()
    sid = made(repo, spec_block())[0]["id"]
    # Someone took the name after the card was made.
    subprocess.run(["git", "-C", str(repo), "branch", sid], check=True)
    with patch.object(work, "ChatSession", Worker):
        assert web.post(f"/api/specs/{sid}/start", json={}).status_code == 409
    spec = specs.load("proj", sid)
    assert spec["state"] == "정리됨" and spec["worktree"] is None and len(spec["history"]) == 1
    assert not work._busy


def test_the_work_session_gets_the_spec_as_its_system_prompt(repo):
    """The stand-in is the CLI itself: the evidence is the argument it got."""

    fake = '''import json, sys
sys.stdin.readline()
print(json.dumps({"type": "result", "result": "saw it", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''
    commands, real_popen = [], subprocess.Popen

    def spawn(command, **kwargs):
        # `subprocess` is one module: `git` goes through here too.
        if command[0] != "claude":
            return real_popen(command, **kwargs)
        commands.append(command)
        return real_popen([sys.executable, "-X", "utf8", "-c", fake], **kwargs)

    sid = made(repo, spec_block())[0]["id"]
    try:
        with patch.object(chat_session.subprocess, "Popen", spawn), \
             patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
            path = started(client(), sid)
    finally:
        work.close_all()
    system = commands[0][commands[0].index("--append-system-prompt") + 1]
    assert system.startswith("Task: carry out the spec") and "로그인 뒤 원래 페이지로 돌아간다" in system
    assert json.loads(system.split("```json\n")[1].split("```")[0])["done"][0] == PASS
    spec = specs.load("proj", sid)
    assert spec["state"] == "작업 중" and spec["worktree"] == path
    rows = work.recall(Path(path))
    assert [(r["role"], r["text"]) for r in rows] == [("user", "Start."), ("assistant", "saw it")]


def test_a_passing_report_is_not_believed_over_a_failing_gate_or_an_uncommitted_change(repo):
    web = client()
    remote = Remote()

    def dirty(path, halt):
        (path / "left.txt").write_text("x", encoding="utf-8")
        return report(True, True)

    Worker.replies = [report(True, True)]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote):
        _adapter(repo, FAIL)
        sid = made(repo, spec_block(slug="gate"))[0]["id"]
        path = started(web, sid)
        spec = specs.load("proj", sid)
        assert spec["state"] == "작업 중" and not spec["gate"]["ok"] and "로 끝났다" in spec["gate"]["reason"]
        assert spec["gate"]["tail"], "게이트 출력의 꼬리가 명세에 남는다"
        assert not remote.created() and remote.pushes() == 0

        _adapter(repo, PASS)
        sid = made(repo, spec_block(slug="dirty"))[0]["id"]
        Worker.replies = [dirty]
        path = started(web, sid)
        spec = specs.load("proj", sid)
        assert spec["gate"]["reason"] == "커밋 안 된 변경" and not remote.created()

        # A report with an item not passed is not judged at all.
        sid = made(repo, spec_block(slug="half"))[0]["id"]
        Worker.replies = [report(True, False)]
        path = started(web, sid)
        assert specs.load("proj", sid)["gate"] is None and not remote.created()
        assert any("통과하지 못했거나" in s["text"] for s in work.recall(Path(path))[-1]["steps"])


def test_a_stop_publishes_nothing_even_after_a_fast_gate_passed(repo):
    import threading

    # The stop comes while a gate shorter than the one-second wait runs.
    halt = threading.Event()
    threading.Timer(0.2, halt.set).start()
    code, _, cut = specs.gate('python -c "import time; time.sleep(0.6)"', repo, halt)
    assert (code, cut) == (None, "사람이 멈춤")

    # The stop comes after the gate passed: neither the push nor the PR.
    def stopped(path, halt):
        halt.set()
        return report(True, True)

    web, remote = client(), Remote()
    passed = {"ok": True, "reason": "", "cmd": PASS, "tail": "ok", "head": "", "ts": 0}
    Worker.replies = [stopped]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs, "judge", return_value=passed):
        sid = made(repo, spec_block(slug="stop"))[0]["id"]
        path = started(web, sid)
        spec = specs.load("proj", sid)
        assert spec["state"] == "작업 중" and spec["fault"].startswith("사람이 멈춤")
        assert remote.pushes() == 0 and not remote.created() and path

        # The stop comes while the push runs: the pull request is not made.
        halts = []

        def passing(path, halt):
            halts.append(halt)
            return report(True, True)

        remote.on_push = lambda: halts[-1].set()
        Worker.replies = [passing]
        sid = made(repo, spec_block(slug="stop-late"))[0]["id"]
        started(web, sid)
        assert remote.pushes() == 1 and not remote.created()
        assert "PR 은 만들지 않았다" in specs.load("proj", sid)["fault"]


ENGLISH_PLAN = ("# Plan\n\n## Requirements\n\n| # | Need | Status |\n| --- | --- | --- |\n"
                "| 2 | Other table | Complete — PR #7 |\n\n"
                "## Steps\n\n| # | Step | Status |\n| --- | --- | --- |\n| 2 | Login | Not started |\n"
                "| 3 | Logout | Not started |\n")


@pytest.mark.parametrize("cell, row, n, done", [
    ("Complete — PR #7", "2", 7, True),
    ("Done — PR #7", "2", 7, True),          # English plans from before `Complete`
    ("완료 — PR #7", "2", 7, True),           # a Korean status cell
    ("Complete — PR #71", "2", 7, False),     # another pull request
    ("Complete — PR #7", "3", 7, False),      # another row
    ("Not completed — PR #7", "2", 7, False),
])
def test_an_english_plan_row_is_bound_to_its_steps_row_and_pr(tmp_path, cell, row, n, done):
    """The requirements table's row `2` already says `Complete — PR #7`, and it
    is not the step: only the steps table counts."""

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    plan = tmp_path / "docs/plans/p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(ENGLISH_PLAN.replace("| 2 | Login | Not started |", f"| 2 | Login | {cell} |"),
                    encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "plan"], check=True)
    ref = {"path": "docs/plans/p.md", "row": row}
    assert specs.row_done(tmp_path, ref, n) is done
    assert specs.marker(tmp_path, ref) == "Complete"


def test_the_marker_follows_the_plan_language_and_rows_come_from_steps(tmp_path):
    plans = tmp_path / "docs/plans"
    plans.mkdir(parents=True)
    (plans / "ko.md").write_text("## 단계\n\n| # | 무엇 | 상태 |\n| --- | --- | --- |\n| 1 | 로그인 | 미착수 |\n",
                                 encoding="utf-8")
    (plans / "en.md").write_text(ENGLISH_PLAN, encoding="utf-8")
    assert specs.marker(tmp_path, {"path": "docs/plans/ko.md"}) == "완료"
    assert specs.marker(tmp_path, {"path": "docs/plans/missing.md"}) == "Complete"
    assert specs.plan_row(tmp_path, {"path": "docs/plans/en.md", "row": "3"})
    # Row `9` exists only in the requirements table.
    (plans / "en.md").write_text(ENGLISH_PLAN.replace("| 2 | Other table", "| 9 | Other table"), encoding="utf-8")
    assert specs.plan_row(tmp_path, {"path": "docs/plans/en.md", "row": "9"}) is None


def test_a_passing_gate_opens_the_pr_and_the_plan_row_follows(repo):
    plan = repo / "docs/plans/p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("## 단계\n\n| # | 무엇 | 상태 |\n| --- | --- | --- |\n| 2 | 로그인 | 미착수 |\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "plan"], check=True)
    web = client()
    remote = Remote()

    def commit_row(path, halt):
        row = path / "docs/plans/p.md"
        row.write_text(row.read_text(encoding="utf-8").replace("| 미착수 |", "| 완료 — PR #7 |"), encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "commit", "-qam", "row"], check=True)
        return "행을 고쳤다"

    def commit_row_then_stop(path, halt):
        said = commit_row(path, halt)
        halt.set()
        return said

    # The row's turn first answers without the edit; the next commits it but
    # is stopped before the push; the one after pushes what is committed.
    Worker.replies = [report(True, True), "행을 고쳤다", commit_row_then_stop, "다시"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote):
        sid = made(repo, spec_block(plan={"path": "docs/plans/p.md", "row": "2"}))[0]["id"]
        assert made(repo, spec_block(slug="x", plan={"path": "docs/plans/p.md", "row": "9"}))[0]["id"]
        assert specs.load("proj", "x")["source"]["plan"] is None, "없는 행은 출처가 아니다"
        first = web.post(f"/api/specs/{sid}/start", json={}).json()
        path = first["path"]
        until(lambda: work._runs[path].turn != first["turn"])
        settled(path)

        spec = specs.load("proj", sid)
        assert spec["state"] == "PR #7" and spec["pr"]["number"] == 7 and spec["pr"]["base"] == "main"
        assert spec["gate"]["ok"] and spec["plan_commit"] == "asked" and "완료 — PR #7" in spec["fault"]
        assert KICKED == [], "계획 행 커밋 전에는 리뷰 루프가 받지 않는다"
        assert remote.pushes() == 1, "행을 고치지 않은 턴은 push 도, 닫기도 하지 않는다"
        create = next(c for c in remote.calls if c[:3] == ["gh", "pr", "create"])
        assert create[create.index("--head") + 1] == sid and create[create.index("--title") + 1] == spec["goal"]
        asked = Worker.made[-1].heard[-1]
        assert "`docs/plans/p.md`" in asked and "`2`" in asked and "`완료 — PR #7`" in asked

        parse(web.post("/api/work/say", json={"path": path, "text": "행을 고쳐라"}).text)
        settled(path)
        spec = specs.load("proj", sid)
        assert spec["plan_commit"] == "asked" and spec["fault"].startswith("사람이 멈춤")
        assert remote.pushes() == 1

        parse(web.post("/api/work/say", json={"path": path, "text": "다시"}).text)
        settled(path)
        spec = specs.load("proj", sid)
        assert spec["plan_commit"] == "pushed" and spec["fault"] is None
        assert remote.pushes() == 2, "PR 을 올릴 때, 계획 행 커밋 뒤에"
        # The loop takes the pull request only once the row's commit is up —
        # after the turn let go of the worktree, so it is waited for.
        until(lambda: KICKED)
        assert [k for k, _ in KICKED] == [sid] and KICKED[0][1]["plan_commit"] == "pushed"

        result = [r for r in chat.recall("next") if r["role"] == "result"]
        assert [r["text"] for r in result] == [f"PR #7 — {spec['goal']}. 완료 조건 2개 통과"]

        # Merged on GitHub: noticed when the list is read, and told once.
        remote.merged = True
        listed = web.get("/api/specs").json()
        assert next(s for s in listed["specs"] if s["id"] == sid)["state"] == "머지됨"
        web.get("/api/specs")
        result = [r["text"] for r in chat.recall("next") if r["role"] == "result"]
        assert result[-1] == f"PR #7 머지됨 — {spec['goal']}" and len(result) == 2


def test_the_pr_body_gives_harvest_its_what_and_why():
    spec = {"id": "fix-login", "repo": "proj", "goal": "로그인 뒤 원래 페이지로 돌아간다",
            "decisions": [{"what": "서버에서 돌린다", "why": "화면은 기록을 모른다", "rejected": "history"}],
            "report": [{"item": PASS, "pass": True, "evidence": "git version 2"}],
            "gate": {"cmd": PASS, "tail": "git version 2"},
            "grounds": {"pages": ["craft/x"], "files": ["a.txt:1"], "rules": []}}
    _, text = harvest.record({"number": 7, "title": spec["goal"], "body": specs.body_of(spec),
                              "headRefName": "fix-login", "mergedAt": "2026-09-25"}, 0)
    assert "What. 로그인 뒤 원래 페이지로 돌아간다" in text
    assert "Why. 서버에서 돌린다 — 화면은 기록을 모른다 (버린 것: history)" in text


def test_the_pr_goes_up_in_korean_and_the_spec_stays_english(repo):
    """GitHub is read by a person; the spec is read by the agents."""

    def korean(texts, direction, deadline):
        assert direction == "en->ko"
        return [f"KO({t})" if t else t for t in texts]

    web, remote = client(), Remote()
    Worker.replies = [report(True, True)]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs.translate, "translate", korean):
        sid = made(repo, spec_block(goal="Return to the page after login"))[0]["id"]
        started(web, sid)
    create = next(c for c in remote.calls if c[:3] == ["gh", "pr", "create"])
    assert create[create.index("--title") + 1] == "KO(Return to the page after login)"
    assert "## 변경 요약\n\nKO(Return to the page after login)" in remote.body
    assert "- KO(서버에서 돌린다) — KO(화면은 기록을 모른다) (버린 것: KO(history))" in remote.body
    assert "- [x] KO(항목 1) — ran 1 · ok" in remote.body, "명령과 그 출력은 옮기지 않는다"
    assert specs.load("proj", sid)["goal"] == "Return to the page after login"
