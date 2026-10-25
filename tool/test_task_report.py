"""A task whose worker committed its work but sent no `done-report`: the server
asks once, a second miss stops with the reason, and a person's turn is never
chased or counted as the answer."""

import json
from unittest.mock import patch

from main import specs, work
from test_main import client, no_machine_settings, settled  # noqa: F401
from test_specs import PASS, SWEPT, Remote, Worker, made, repo as repository_fixture, spec_block, started
from test_task_workflow import git, until

repo = repository_fixture


def committed(text: str):
    """A worker turn that commits its work, leaves the tree clean and says `text`."""

    def reply(path, halt):
        (path / "a.txt").write_text("done\n", encoding="utf-8")
        git(path, "commit", "-qam", "work")
        return text
    return reply


def test_a_turn_without_a_report_or_a_question_asks_for_the_report_once(repo):
    # Live (ai-companion-voice-agent): a Codex worker read the pending server gate as a failed item,
    # withheld its `done-report`, and the task sat in `작업 중` with nothing running.
    web, remote = client(), Remote()
    report = [{"item": PASS, "pass": True}, {"item": "돌아오는 테스트가 있다", "pass": True}]
    Worker.replies = [committed("The server gate is pending, so I cannot emit a done-report yet."),
                      SWEPT + "```done-report\n" + json.dumps(report) + "\n```"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs, "korean", side_effect=lambda spec: spec):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("pr") and s)
        assert saved["pr"]["number"] == 7 and not saved["report_asked"]
        assert "pending server gate is not a failed item" in work._sessions[str(repo)].heard[-1]
    work.close_all()


def test_a_second_turn_without_a_report_stops_with_the_reason_and_a_persons_turn_is_not_chased(repo):
    web = client()
    Worker.replies = [committed("Still pending."), "Still pending."]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("fault") and s)
        assert saved["state"] == "작업 중" and "다시 빠졌다" in saved["fault"] and not saved["report_asked"]
        assert not Worker.replies, "asked once, then stopped instead of waiting silently"
        # A person's own turn may end in plain talk: no report request follows it.
        Worker.replies = ["Here is what the helper does.", "unsent"]
        specs.update(repo.name, sid, fault=None)
        settled(str(repo))
        assert web.post("/api/work/say", json={"path": str(repo), "text": "What does it do?"}).status_code == 200
        settled(str(repo))
        saved = specs.load(repo.name, sid)
        assert Worker.replies == ["unsent"] and not saved["fault"] and not saved["report_asked"]
    work.close_all()


def test_a_queued_order_is_a_persons_turn(repo):
    # Review round 1: an instruction sent through the queue ran without the person mark and was chased.
    seen = []
    with patch.dict(work._queued, {str(repo): work.Order(path=str(repo), text="Explain")}), \
         patch.object(work, "ChatSession", Worker), \
         patch.object(work, "begin", side_effect=lambda path, chat, text, release, run: seen.append(run) or release()):
        work.dispatch(repo, work.Run(Worker(repo)))
    assert [r.person for r in seen] == [True]
    work.close_all()


def test_a_report_request_a_person_kept_from_going_out_is_not_counted_as_asked(repo):
    # Review round 1: a person took the worktree before the request went out; it stayed marked as asked,
    # and that person's own plain answer was then failed as the second miss.
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        committed("")(repo, None)
        assert specs.check(repo, work.Run(Worker(repo)), "The gate is pending.") is None, "a driver's turn"
        follow = specs.check(repo, work.Run(Worker(repo), task=True), "The gate is pending.")
        assert specs.load(repo.name, sid)["report_asked"]
        release = specs.hold(work._busy, specs._lock, str(repo), "", kind="turn")   # the person's turn
        try:
            follow()
        finally:
            release()
        saved = specs.load(repo.name, sid)
        assert not saved["report_asked"] and "보내지 못했다" in saved["fault"], "never sent, so never asked"
        # Even with a request out, a person's plain answer is not the answer to it.
        specs.update(repo.name, sid, report_asked=True, fault=None)
        assert specs.check(repo, work.Run(Worker(repo), person=True), "Here is the explanation.") is None
        saved = specs.load(repo.name, sid)
        assert saved["report_asked"] and not saved["fault"]
    work.close_all()
