"""oneshot step 2: intake, the environment card, the ledger and the empty-folder bootstrap.

See `docs/plans/oneshot/2-bootstrap.md`. Model turns (`oneshot.ask`) and GitHub
(`oneshot.gh`, `oneshot.remote_id`, `oneshot.gh_user`) are stand-ins; Git runs,
and `origin` is a local bare repository.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import channels, connect, loop, oneshot, oneshot_profile, oneshot_spec, runtime, specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse

LAUNCH = oneshot.launch   # the real one; `world` replaces it with a synchronous state change

INTENT = {"restated": "가계부를 만든다.", "kind": "webapp", "name": "Budget Tracker", "users": "나 혼자 쓴다.",
          "first_screen": "이번 달 지출을 본다."}
PROFILE = {**{k: f["default"] for k, f in oneshot.SCHEMA.items()}, "visibility": "private", "docs_language": "ko",
           "github_owner": "someone"}


CODEX = [{"id": "codex:gpt-6.1-sol", "is_default": True, "efforts": [{"id": e} for e in ("", "high", "xhigh")]},
         {"id": "codex:gpt-other", "efforts": [{"id": e} for e in ("", "low")]}]


def fenced(name: str, value) -> str:
    return f"알겠다.\n\n```{name}\n{json.dumps(value, ensure_ascii=False)}\n```\n"


class GitHub:
    """`gh` for one owner: repositories by name, each with a node id once created."""

    def __init__(self, bare: Path):
        self.repos: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.bare = bare
        self.commits = (0, "[]", "")   # returncode, stdout, stderr of listing an adopted repository's commits

    def gh(self, args, cwd=None, timeout=120):
        self.calls.append(args)
        if args[:2] == ["repo", "create"]:
            self.repos[args[2]] = f"R_{len(self.repos) + 1}"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(self.bare)], check=True, capture_output=True)
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["api", "repos/someone/budget-tracker/commits"]:
            return subprocess.CompletedProcess(args, *self.commits)
        return subprocess.CompletedProcess(args, 1, "", "unexpected")

    def remote_id(self, owner, name):
        self.calls.append(["view", f"{owner}/{name}"])
        return self.repos.get(f"{owner}/{name}", "")


@pytest.fixture
def world(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hub = Path(oneshot.ROOT)
    github = GitHub(tmp_path / "remote.git")
    identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                for what, value in (("NAME", "Oneshot test"), ("EMAIL", "oneshot@example.test"))}
    examined = []
    with patch.dict(os.environ, identity), patch.object(oneshot, "RUNS", tmp_path / "oneshot"), \
         patch.object(oneshot, "PROFILES", tmp_path / "oneshot" / "profiles"), \
         patch.object(connect, "RECORDS", tmp_path / "connect"), \
         patch.object(connect, "examine", lambda path, then=None: examined.append(path) or True), \
         patch.object(channels, "WORKSPACE", workspace), \
         patch.object(oneshot, "gh", github.gh), patch.object(oneshot, "remote_id", github.remote_id), \
         patch.object(oneshot, "gh_user", lambda: "someone"), \
         patch.object(oneshot, "url_of", lambda run: str(github.bare)), \
         patch.object(oneshot, "codex_role", lambda role="", store=None: False), \
         patch.object(channels, "codex_models", lambda: CODEX), \
         patch.object(oneshot_spec, "begin", lambda repo: None), \
         patch.object(oneshot, "launch",
                      lambda rid, work, *a, **f: oneshot.changed(rid, **f, working=work, stopped=None)):
        yield {"workspace": workspace, "github": github, "hub": hub, "tmp": tmp_path, "examined": examined}


def made(world, **extra) -> str:
    rid = "0123456789abcdef"
    oneshot.save({"id": rid, "version": oneshot.VERSION, "created": 0, "state": "bootstrap", "working": "bootstrap", "stopped": None,
                  "sentence": "가계부 만들어줘", "picked": "", "intent": {**INTENT, "name": "budget-tracker"},
                  "confirmed": True, "profile": dict(PROFILE), "proposed": {},
                  "identity": {"path": str(world["workspace"] / "budget-tracker"), "name": "budget-tracker",
                               "owner": "someone", "spec_id": oneshot.BRANCH, "branch": oneshot.BRANCH},
                  "steps": {}, "github": {}, "base": None, "adopt": False, "spend": oneshot.ledger(None), "spec": None,
                  **extra})
    return rid


# -- Blocks and the schema ------------------------------------------------------------

def test_intent_block_is_checked_and_the_name_becomes_a_task_name():
    assert oneshot.intent_of(INTENT)["name"] == "budget-tracker"
    for broken in ({**INTENT, "kind": "game"}, {**INTENT, "restated": " "}, {**INTENT, "name": "!!!"}, []):
        with pytest.raises(ValueError):
            oneshot.intent_of(broken)


def test_profile_block_refuses_fields_options_and_choices_outside_the_schema():
    assert oneshot.proposal({"llm": {"value": "anthropic", "why": "AI 요약이 있다"}})["llm"]["value"] == "anthropic"
    refused = [{"colour": {"value": "red", "why": "-"}},
               {"llm": {"value": "mistral", "why": "-"}},
               {"backend": {"value": "node", "why": "-"}},          # the stack is S2's, not the card's
               {"visibility": {"value": "public", "why": "-"}},     # the person's to decide
               {"github_owner": {"value": "someone", "why": "-"}},
               {"llm": {"value": "anthropic"}}]
    for block in refused:
        with pytest.raises(ValueError):
            oneshot.proposal(block)


def test_validity_rules_on_the_card():
    assert oneshot.problems(PROFILE) == ([], [])
    errors, _ = oneshot.problems({**PROFILE, "visibility": ""})
    assert any("visibility" in e for e in errors)
    errors, _ = oneshot.problems({**PROFILE, "visibility": "public", "license": "none"})
    assert any("라이선스" in e for e in errors)
    errors, _ = oneshot.problems({**PROFILE, "ci": "github-actions"})
    assert any("CI" in e for e in errors)


def test_the_card_holds_no_stack_and_a_card_saved_with_one_loads_without_it():
    assert not set(oneshot_profile.PARTS) & set(oneshot.SCHEMA)
    assert not {"python_env", "conda_env", "js_pm"} & set(oneshot.SCHEMA)
    old = {**PROFILE, "backend": "fastapi", "frontend": "vite-react", "python_env": "conda", "conda_env": "x"}
    assert oneshot_profile.current(old) == PROFILE


# -- The ledger -------------------------------------------------------------------------

def test_ledger_charges_estimates_and_meters_codex_by_hours():
    spend = oneshot.ledger(None)
    with patch.object(oneshot_profile, "estimate", lambda role: 2.0):
        spend = oneshot.charged(spend, "builder", False, None, 10)        # unreported: the estimate, marked unknown
        assert spend["usd"] == 2.0 and spend["unknown"] == 1
        spend = oneshot.charged(spend, "builder", False, 30.0, 10)
        assert spend["usd"] == 32.0 and spend["max"]["builder"] == 30.0
        for _ in range(3):
            spend = oneshot.charged(spend, "reviewer", True, None, 100)       # Codex: no USD invented
        assert spend["usd"] == 32.0 and spend["calls"] == 5 and spend["seconds"] == 320
    restored = json.loads(json.dumps(spend))
    assert oneshot.ledger(restored) == spend


# -- Intake and the card ------------------------------------------------------------------

def test_intake_restates_and_a_refused_block_is_asked_again_once(world):
    replies = [fenced("oneshot-intent", {**INTENT, "kind": "game"}), fenced("oneshot-intent", INTENT)]
    heard = []

    def ask(store, role, system, text, where=None, images=()):
        heard.append(text)
        return replies.pop(0), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        started = oneshot.start(oneshot.Start(sentence="가계부 만들어줘"))
        oneshot.intake(started["id"])
    run = oneshot.load(started["id"])
    assert run["intent"]["name"] == "budget-tracker" and run["sentence"] == "가계부 만들어줘"
    assert "was refused" in heard[1] and run["spend"]["calls"] == 2


def test_two_refused_blocks_stop_with_block(world):
    with patch.object(oneshot, "ask", lambda *a: ("블록 없음", {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "block"


def test_a_picked_folder_must_be_empty(world):
    full = world["tmp"] / "picked"
    full.mkdir()
    (full / ".hidden").write_text("x")
    with pytest.raises(HTTPException) as refused:
        oneshot.start(oneshot.Start(sentence="가계부", path=str(full)))
    assert refused.value.status_code == 400


def test_confirming_the_intent_reserves_the_name_and_refuses_conflicts(world):
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-intent", INTENT), {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    connect.RECORDS.mkdir(parents=True)
    record = connect.RECORDS / "budget-tracker.json"
    record.write_text('{"path": "elsewhere", "hash": "abc"}', encoding="utf-8")
    before = record.read_bytes()
    with pytest.raises(HTTPException) as refused:
        oneshot.intent(rid, oneshot.Intent(confirm=True))
    assert refused.value.status_code == 409 and record.read_bytes() == before
    assert not any(c[:2] == ["repo", "create"] for c in world["github"].calls)
    record.unlink()
    world["github"].repos["someone/budget-tracker"] = "R_9"
    with pytest.raises(HTTPException):
        oneshot.intent(rid, oneshot.Intent(confirm=True))
    del world["github"].repos["someone/budget-tracker"]
    shown = oneshot.intent(rid, oneshot.Intent(confirm=True))
    assert shown["identity"]["name"] == "budget-tracker" and shown["state"] == "environment"
    other = oneshot.start(oneshot.Start(sentence="가계부 둘"))["id"]
    assert "다른 원샷 실행" in "; ".join(oneshot.conflicts(other, world["workspace"] / "budget-tracker",
                                                          "budget-tracker", "someone"))


def test_unsupported_kind_ends_the_intake(world):
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-intent", {**INTENT, "kind": "cli"}), {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="CLI 도구"))["id"]
        oneshot.intake(rid)
    with pytest.raises(HTTPException) as refused:
        oneshot.intent(rid, oneshot.Intent(confirm=True))
    assert "webapp" in refused.value.detail


def test_the_card_is_prefilled_from_the_kind_and_confirmed_only_without_errors(world):
    oneshot.PROFILES.mkdir(parents=True)
    # saved before the stack left the card: `styling` loads dropped
    (oneshot.PROFILES / "webapp.json").write_text(json.dumps({"styling": "css", "ui_language": "en",
                                                              "visibility": "public"}), "utf-8")
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-intent", INTENT), {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    oneshot.intent(rid, oneshot.Intent(confirm=True))
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-profile", {"llm": {"value": "anthropic",
                                                                                     "why": "요약"}}), {}, False)):
        oneshot.environment(rid)
    run = oneshot.load(rid)
    assert run["profile"]["ui_language"] == "en" and run["profile"]["llm"] == "anthropic"
    assert "styling" not in run["profile"]
    assert run["profile"]["github_owner"] == "someone"
    with pytest.raises(HTTPException):
        oneshot.profile(rid, oneshot.Profile(values={}, confirm=True))       # docs language empty
    with pytest.raises(HTTPException):
        oneshot.profile(rid, oneshot.Profile(values={"colour": "red"}))
    shown = oneshot.profile(rid, oneshot.Profile(values={"docs_language": "ko", "license": "mit"}, confirm=True))
    assert shown["state"] == "bootstrap" and shown["confirmed"]


def test_a_card_saved_before_a_field_was_dropped_shows_and_saves_without_it(world):
    rid = made(world, state="environment", working=None, confirmed=False,
               profile={**PROFILE, "budget_usd": 50, "budget_hours": 10})
    shown = oneshot.view(oneshot.load(rid))
    assert "budget_usd" not in shown["profile"] and shown["profile"]["github_owner"] == "someone"
    # What the screen sends back is the card it was shown, and it saves.
    with patch.object(oneshot, "launch", lambda rid, *a, **k: oneshot.save({**oneshot.load(rid), **k})):
        saved = oneshot.profile(rid, oneshot.Profile(values=shown["profile"], confirm=True))
    assert "budget_usd" not in saved["profile"] and saved["confirmed"]


# -- Bootstrap ---------------------------------------------------------------------------------

def finished(world, rid: str) -> dict:
    oneshot.bootstrap(rid)
    return json.loads(oneshot.file_of(rid).read_text(encoding="utf-8"))


def test_bootstrap_makes_the_repository_the_spec_and_the_linked_worktree(world):
    rid = made(world)
    run = finished(world, rid)
    assert run["committed"] and run["spec"] == {"repo": "budget-tracker", "id": oneshot.BRANCH}
    repo = world["workspace"] / "budget-tracker"
    assert subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True,
                          text=True).stdout.strip() == "main"
    assert oneshot.tracked(repo) == {".gitignore", "README.md", ".wiki/adapter.toml", ".gitattributes"}
    # Nothing stack-shaped before S2: the gate waits for the tests turn's command, the README for the stack.
    assert 'gate_cmd = ""' in (repo / ".wiki/adapter.toml").read_text(encoding="utf-8")
    assert "## Setup" not in (repo / "README.md").read_text(encoding="utf-8")
    assert "node_modules/" in (repo / ".gitignore").read_text(encoding="utf-8")
    spec = specs.load("budget-tracker", oneshot.BRANCH)
    assert spec["oneshot"]["run"] == rid and spec["workspace_mode"] == "worktree"
    assert Path(spec["worktree"]).is_dir() and spec["done"] == []
    assert json.loads((connect.RECORDS / "budget-tracker.json").read_text(encoding="utf-8"))["hash"]
    assert world["examined"] == [repo]          # connected as `[연결]` would: the hosts' tests and Codex's trust
    assert json.loads((oneshot.PROFILES / "webapp.json").read_text(encoding="utf-8"))["github_owner"] == "someone"
    with pytest.raises(RuntimeError):
        oneshot.save(run)                                                    # never written again


@pytest.mark.parametrize("cut", oneshot.STEPS)
def test_a_cut_after_each_effect_resumes_without_repeating_it(world, cut):
    rid = made(world)
    real = oneshot.SUBSTEPS[cut]

    def effect_then_crash(run):
        real(run)
        raise RuntimeError("server stopped before the record")

    with patch.dict(oneshot.SUBSTEPS, {cut: effect_then_crash}):
        oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"] and cut not in oneshot.load(rid)["steps"]
    run = finished(world, rid)
    assert run["committed"]
    creates = [c for c in world["github"].calls if c[:2] == ["repo", "create"]]
    assert len(creates) == 1
    repo = world["workspace"] / "budget-tracker"
    assert subprocess.run(["git", "rev-list", "--count", "main"], cwd=repo, capture_output=True,
                          text=True).stdout.strip() == "1"
    assert len(list((specs.SPECS / "budget-tracker").glob("*.json"))) == 1


def test_an_unrecorded_same_name_remote_stops_and_is_adopted_only_on_request(world):
    rid = made(world, steps={s: 1 for s in ("recheck", "folder", "files", "commit", "register")})
    for s in ("folder", "files", "commit", "register"):
        oneshot.SUBSTEPS[s](oneshot.load(rid))
    world["github"].repos["someone/budget-tracker"] = "R_7"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(world["github"].bare)], check=True, capture_output=True)
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "github"
    oneshot.changed(rid, adopt=True, stopped=None)
    run = finished(world, rid)
    assert run["committed"] and run["github"]["node_id"] == "R_7"
    assert not any(c[:2] == ["repo", "create"] for c in world["github"].calls)


def test_a_spec_that_is_not_this_runs_stops_instead_of_being_overwritten(world):
    rid = made(world)
    specs.SPECS.joinpath("budget-tracker").mkdir(parents=True)
    foreign = {"id": oneshot.BRANCH, "repo": "budget-tracker", "history": [{"ts": 1, "state": "정리됨"}]}
    (specs.SPECS / "budget-tracker" / f"{oneshot.BRANCH}.json").write_text(json.dumps(foreign), encoding="utf-8")
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "folder"
    assert json.loads((specs.SPECS / "budget-tracker" / f"{oneshot.BRANCH}.json").read_text(encoding="utf-8")) == foreign


@pytest.mark.parametrize("steps, refused", [
    (("recheck", "folder", "files"), True),   # its README and adapter carry the old card's stack
    (("recheck", "folder"), True),            # cut while writing them, before `files` was recorded
    (("recheck",), False),                    # nothing written yet: the new bootstrap writes the new files
])
def test_a_version_2_run_whose_bootstrap_wrote_files_is_refused_on_resume(world, steps, refused):
    rid = made(world, version=2, steps={s: 1 for s in steps}, working=None,
               stopped={"reason": "restart", "detail": "", "ts": 0})
    for s in steps[1:]:
        oneshot.SUBSTEPS[s](oneshot.load(rid))
    if steps == ("recheck", "folder"):
        (world["workspace"] / "budget-tracker" / "README.md").write_text("# old\n\n## Setup\n", encoding="utf-8")
    if refused:
        with pytest.raises(HTTPException, match="새 실행"):
            oneshot.resume(rid, oneshot.Resume())
        assert oneshot.load(rid)["stopped"]
    else:
        assert oneshot.resume(rid, oneshot.Resume())["working"] == "bootstrap"
        wrote_then_stopped_and_resumed(rid)


def wrote_then_stopped_and_resumed(rid: str) -> None:
    """The bootstrap writes its files through `step`, the server stops, and the run resumes after them."""

    for s in ("folder", "files"):
        oneshot.step(rid, s, oneshot.SUBSTEPS[s])
    assert oneshot.load(rid)["version"] == oneshot.VERSION   # what it wrote is this version's
    oneshot.save({**oneshot.load(rid), "working": None, "stopped": {"reason": "restart", "detail": "", "ts": 0}})
    assert oneshot.resume(rid, oneshot.Resume())["working"] == "bootstrap"


def test_a_version_2_run_waiting_at_the_card_writes_this_versions_files_and_resumes_after_them(world):
    rid = made(world, version=2, state="environment", working=None, steps={"recheck": 1})
    assert oneshot.profile(rid, oneshot.Profile(values=dict(PROFILE), confirm=True))["state"] == "bootstrap"
    wrote_then_stopped_and_resumed(rid)


def test_a_file_someone_changed_is_never_overwritten(world):
    rid = made(world)
    repo = world["workspace"] / "budget-tracker"
    repo.mkdir()
    (repo / "README.md").write_text("mine", encoding="utf-8")
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "folder"
    assert (repo / "README.md").read_text(encoding="utf-8") == "mine"


def test_recover_stops_running_stages_with_restart(world):
    rid = made(world)
    oneshot.recover()
    assert oneshot.load(rid)["stopped"]["reason"] == "restart" and oneshot.load(rid)["working"] is None


def through(rid: str, last: str) -> None:
    for name in oneshot.STEPS[:oneshot.STEPS.index(last) + 1]:
        oneshot.step(rid, name, oneshot.SUBSTEPS[name])


def git_ok(where: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=where, capture_output=True).returncode == 0


def test_a_file_changed_after_the_files_step_is_never_committed(world):
    rid = made(world)
    through(rid, "files")
    repo = world["workspace"] / "budget-tracker"
    (repo / "README.md").write_text("UNAPPROVED LOCAL EDIT", encoding="utf-8")
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "folder"
    assert not git_ok(repo, "rev-parse", "--verify", "main") and not world["github"].bare.exists()


def test_an_existing_main_with_the_same_names_but_other_content_is_not_adopted(world):
    rid = made(world)
    through(rid, "files")
    repo = world["workspace"] / "budget-tracker"
    (repo / "README.md").write_text("other", encoding="utf-8")
    for args in (["init", "-b", "main"], ["add", "-A"], ["-c", "user.name=x", "-c", "user.email=x@x", "commit", "-m", "x"]):
        assert git_ok(repo, *args)
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "folder" and not world["github"].bare.exists()


def test_an_origin_changed_after_its_step_is_never_pushed_to(world):
    rid = made(world)
    through(rid, "origin")
    repo, other = world["workspace"] / "budget-tracker", world["tmp"] / "other.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(other)], check=True, capture_output=True)
    assert git_ok(repo, "remote", "set-url", "origin", str(other))
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "github"
    assert not git_ok(other, "rev-parse", "--verify", "main")
    assert not git_ok(world["github"].bare, "rev-parse", "--verify", "main")


def test_a_separate_push_url_is_never_pushed_to(world):
    rid = made(world)
    through(rid, "origin")
    repo, other = world["workspace"] / "budget-tracker", world["tmp"] / "other.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(other)], check=True, capture_output=True)
    assert git_ok(repo, "remote", "set-url", "--push", "origin", str(other))
    oneshot.bootstrap(rid)
    assert oneshot.load(rid)["stopped"]["reason"] == "github"
    assert not git_ok(other, "rev-parse", "--verify", "main")
    assert not git_ok(world["github"].bare, "rev-parse", "--verify", "main")


@pytest.mark.parametrize("answer, adopted", [((1, "", "HTTP 403: cannot list commits"), False),
                                             ((0, '[{"sha": "abc"}]', ""), False),
                                             ((1, "", "gh: Git Repository is empty. (HTTP 409)"), True)])
def test_a_remote_is_adopted_only_when_github_shows_it_has_no_commits(world, answer, adopted):
    rid = made(world, adopt=True)
    through(rid, "register")
    world["github"].repos["someone/budget-tracker"] = "R_7"
    world["github"].commits = answer
    subprocess.run(["git", "init", "--bare", "-b", "main", str(world["github"].bare)], check=True, capture_output=True)
    oneshot.bootstrap(rid)
    run = json.loads(oneshot.file_of(rid).read_text(encoding="utf-8"))
    assert bool(run.get("committed")) == adopted
    assert git_ok(world["github"].bare, "rev-parse", "--verify", "main") == adopted


def test_one_worker_per_run_and_a_second_resume_is_refused(world):
    rid = made(world, working=None, stopped={"reason": "restart", "detail": "", "state": "bootstrap"})
    release = threading.Event()
    with patch.object(oneshot, "launch", LAUNCH), patch.object(oneshot, "bootstrap", lambda rid: release.wait(10)):
        try:
            oneshot.resume(rid, oneshot.Resume())
            for again in (lambda: oneshot.resume(rid, oneshot.Resume()), lambda: oneshot.launch(rid, "bootstrap")):
                with pytest.raises(HTTPException) as refused:
                    again()
                assert refused.value.status_code == 409
            assert len(oneshot.close_all(wait=False)) == 1
        finally:
            release.set()
            oneshot.close_all()


def test_a_turn_cut_by_the_server_dying_is_charged_once_as_unknown(world):
    def dies(*a):
        raise SystemExit("the server went down inside the turn")

    with patch.object(oneshot, "ask", dies):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        with pytest.raises(SystemExit):
            oneshot.intake(rid)
    with patch.object(oneshot_profile, "estimate", lambda role: 2.0):
        oneshot.recover()
        oneshot.recover()
    run = oneshot.load(rid)
    assert run["stopped"]["reason"] == "restart" and run["inflight"] is None
    assert (run["spend"]["calls"], run["spend"]["unknown"], run["spend"]["usd"]) == (1, 1, 2.0)


def test_a_long_codex_turn_cut_by_a_restart_keeps_the_time_its_heartbeats_prove(world):
    now = time.time()
    rid = made(world, state="intake", inflight={"role": "intake", "codex": True, "sent": now - 4000, "seen": now - 400})
    oneshot.recover()
    spend = oneshot.load(rid)["spend"]
    assert spend["seconds"] >= 3600 and spend["calls"] == 1 and spend["unknown"] == 1 and spend["usd"] == 0


def test_a_running_turn_writes_heartbeats(world):
    rid = made(world, inflight={"role": "intake", "codex": False, "sent": 1.0})
    ended = threading.Event()
    with patch.object(oneshot, "HEARTBEAT", 0.05):
        beat = threading.Thread(target=oneshot.heartbeat, args=(oneshot.run_store(rid), ended))
        beat.start()
        time.sleep(0.3)
        ended.set()
        beat.join()
    assert oneshot.load(rid)["inflight"]["seen"] > 1.0


def test_a_launch_refused_at_shutdown_leaves_the_run_where_it_was(world):
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-intent", INTENT), {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    with patch.object(oneshot, "launch", LAUNCH), patch.object(oneshot, "environment", lambda rid: None), \
         patch.object(oneshot, "intake", lambda rid, *a: None):
        runtime.stopping.set()
        try:
            for refused_call in (lambda: oneshot.intent(rid, oneshot.Intent(confirm=True)),
                                 lambda: oneshot.start(oneshot.Start(sentence="가계부 둘"))):
                with pytest.raises(HTTPException) as refused:
                    refused_call()
                assert refused.value.status_code == 503
        finally:
            runtime.stopping.clear()
        run = oneshot.load(rid)
        assert run["state"] == "intake" and run["identity"] is None and run["intent"] and not run["working"]
        second = next(r for r in oneshot.runs() if r["id"] != rid)
        assert second["stopped"]["reason"] == "restart"
        oneshot.resume(second["id"], oneshot.Resume())
        assert oneshot.intent(rid, oneshot.Intent(confirm=True))["state"] == "environment"
        oneshot.close_all()


def test_a_failed_turn_keeps_the_cost_it_reported(world):
    def fails(*a):
        raise oneshot.Failed("provider error", {"cost_usd": 30.0})

    with patch.object(oneshot, "ask", fails), patch.object(oneshot_profile, "estimate", lambda role: 2.0):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    run = oneshot.load(rid)
    assert run["stopped"]["reason"] == "block" and run["spend"]["usd"] == 30.0 and run["spend"]["unknown"] == 0


def test_shutdown_starts_no_new_step_and_refuses_new_workers(world):
    rid = made(world)
    runtime.stopping.set()
    try:
        oneshot.bootstrap(rid)
        with patch.object(oneshot, "launch", LAUNCH), pytest.raises(HTTPException) as refused:
            oneshot.launch(rid, "bootstrap")
    finally:
        runtime.stopping.clear()
    run = oneshot.load(rid)
    assert run["stopped"]["reason"] == "restart" and run["steps"] == {} and refused.value.status_code == 503
    assert not (world["workspace"] / "budget-tracker").exists()


# -- The three models ---------------------------------------------------------------------

MODELS = {"spec": {"model": "opus", "effort": "high"}, "build": {"model": "sonnet", "effort": "medium"},
          "review": {"model": "codex:gpt-6.1-sol", "effort": "xhigh"}}
REGISTERED = {"review": lambda: {"model": "codex:loop-default", "effort": "low"},
              "critic": lambda: {"model": "opus", "effort": "high"}}


def window(*a):
    raise AssertionError("a run that fixed its models never reads the window's `next` model")


def test_the_start_fixes_three_models_and_every_role_reads_its_own(world):
    with patch.object(oneshot, "launch", lambda *a, **f: None):
        rid = oneshot.start(oneshot.Start(sentence="가계부", models=MODELS))["id"]
    assert oneshot.load(rid)["models"] == MODELS
    store = oneshot.run_store(rid)
    with patch.dict(oneshot.ROLES, REGISTERED), patch("main.query.config", window):
        for role in ("intake", "profile", "spec", "design", "split", "frontend"):
            assert oneshot.role_config(role, store) == MODELS["spec"], role
        assert oneshot.role_config("build", store) == MODELS["build"]
        assert oneshot.role_config("review", store) == MODELS["review"]
        assert oneshot.role_config("critic", store) == {"model": "opus", "effort": "high"}   # not the owner's to pick
    sent = []

    class Chat:   # the real `ask`, on a session that answers at once
        is_codex = False

        def __init__(self, where, model="", effort="", **k):
            sent.append((model, effort))

        def say(self, text, halt, images):
            yield SimpleNamespace(kind="done", text=fenced("oneshot-intent", INTENT), meta={"cost_usd": 0.1})

        def close(self):
            pass

    with patch.object(oneshot, "ChatSession", Chat):
        oneshot.intake(rid)
    assert sent == [("opus", "high")] and oneshot.load(rid)["intent"]["name"] == "budget-tracker"


def test_an_empty_model_is_the_roles_own_default_and_an_older_run_keeps_the_window(world):
    empty = {"spec": {"model": "", "effort": ""}, "build": {"model": "", "effort": "max"},
             "review": {"model": "", "effort": ""}}
    with patch.object(oneshot, "launch", lambda *a, **f: None):
        rid = oneshot.start(oneshot.Start(sentence="가계부", models=empty))["id"]
    # "The Review Loop's model" is named at the start, from the settings then: Codex's default, `high`.
    assert oneshot.load(rid)["models"]["review"] == {"model": "codex:gpt-6.1-sol", "effort": "high"}
    store = oneshot.run_store(rid)
    later = {"review_model": "opus", "review_effort": "low"}
    with patch.object(loop, "settings", lambda *a: {**loop.DEFAULTS, **later}), patch("main.query.config", window):
        assert oneshot.role_config("intake", store) == {"model": "", "effort": ""}           # the CLI's default
        assert oneshot.role_config("build", store) == {"model": "", "effort": "max"}
        assert oneshot.role_config("review", store) == {"model": "codex:gpt-6.1-sol", "effort": "high"}   # unmoved
    older = made(world)                                                                  # no `models`
    with patch("main.query.config", lambda cid: {"model": "next-model", "effort": "e"}):
        assert oneshot.role_config("intake", oneshot.run_store(older))["model"] == "next-model"


@pytest.mark.parametrize("role", [{"model": "opus; rm -rf", "effort": ""}, {"model": "opus", "effort": "HIGH!"},
                                  {"model": "opus", "effort": "banana"}, {"model": "codex:", "effort": ""},
                                  {"model": "codex:not-a-listed-model", "effort": "max"},
                                  {"model": "codex:gpt-other", "effort": "high"}])
def test_a_model_or_effort_the_cli_would_not_take_is_refused_before_the_run_exists(world, role):
    with pytest.raises(HTTPException) as refused:
        oneshot.start(oneshot.Start(sentence="가계부", models={**MODELS, "build": role}))
    assert refused.value.status_code == 400 and not list(oneshot.RUNS.glob("*.json"))


@pytest.mark.parametrize("role", [{"model": "opus[1m]", "effort": ""}, {"model": "codex:gpt-other", "effort": "low"},
                                  {"model": "", "effort": "max"}])
def test_a_listed_or_typed_model_and_its_own_effort_are_taken(world, role):
    with patch.object(oneshot, "launch", lambda *a, **f: None):
        rid = oneshot.start(oneshot.Start(sentence="가계부", models={**MODELS, "build": role}))["id"]
    assert oneshot.load(rid)["models"]["build"] == role


def test_the_spec_holds_the_runs_models_and_its_work_cell_never_moves(world):
    rid = made(world, models=MODELS)
    finished(world, rid)
    spec = specs.load("budget-tracker", oneshot.BRANCH)
    assert spec["oneshot"]["models"] == MODELS
    assert spec["cell"] == {**MODELS["build"], "fast": False} and spec["reviewer"] == MODELS["review"]
    made_with = []

    class Chat:
        is_codex, bypass, fast = False, True, False

        def __init__(self, path, model="", effort="", **k):
            made_with.append((model, effort))

        def reconfigure(self, model, effort):
            made_with.append((model, effort))

    with patch.object(work, "ChatSession", Chat), patch.dict(work._sessions, clear=True):
        work.session(Path(spec["worktree"]), "haiku", "low")       # the screen's toolbar, or a repair turn
    assert made_with == [("sonnet", "medium")]
    assert specs.load("budget-tracker", oneshot.BRANCH)["cell"] == {**MODELS["build"], "fast": False}


def test_a_default_build_model_is_not_filled_in_by_the_review_loops_start(world):
    default = {**MODELS, "build": {"model": "", "effort": ""}}
    finished(world, made(world, models=default))
    spec = specs.load("budget-tracker", oneshot.BRANCH)
    screen = {"model": "codex:gpt-6.1-sol", "effort": "high", "fast": False}   # the work pane's pick, sent with `[시작]`
    assert loop.named(world["workspace"] / "budget-tracker", spec, screen)["cell"] == {"model": "", "effort": "", "fast": False}
    made_with = []

    class Chat:
        is_codex, bypass, fast = False, True, False

        def __init__(self, path, model="", effort="", **k):
            made_with.append((model, effort))

    with patch.object(work, "ChatSession", Chat), patch.dict(work._sessions, clear=True):
        work.session(Path(spec["worktree"]), "codex:gpt-6.1-sol", "high")   # the repair turn after it
    assert made_with == [("", "")]


def test_a_named_review_model_keeps_its_default_effort_after_the_pr(world, tmp_path):
    fixed = {**MODELS, "review": {"model": "opus", "effort": ""}}
    finished(world, made(world, models=fixed))
    spec = {**specs.load("budget-tracker", oneshot.BRANCH), "pr": {"number": 7, "head": "abc"}}
    made_with = []

    class Chat:
        is_codex = False

        def __init__(self, path, model="", effort="", **k):
            made_with.append((model, effort))

    with patch.object(loop, "ChatSession", Chat), patch.object(loop, "REVIEW", tmp_path / "review"), \
         patch.dict(loop._cells, clear=True), patch.object(loop, "settings", lambda *a: {**loop.DEFAULTS, "review_effort": "high"}):
        loop.cell(spec, tmp_path)
    assert made_with == [("opus", "")]   # the CLI's default, as before the PR — never the settings' `high`
