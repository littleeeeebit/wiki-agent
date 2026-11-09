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
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import channels, connect, oneshot, oneshot_profile, runtime, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse

LAUNCH = oneshot.launch   # the real one; `world` replaces it with a synchronous state change

INTENT = {"restated": "가계부를 만든다.", "kind": "webapp", "name": "Budget Tracker", "users": "나 혼자 쓴다.",
          "first_screen": "이번 달 지출을 본다."}
PROFILE = {**{k: f["default"] for k, f in oneshot.SCHEMA.items()}, "visibility": "private", "docs_language": "ko",
           "github_owner": "someone", "budget_usd": 50, "budget_hours": 10, "python_env": "uv"}


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
    with patch.dict(os.environ, identity), patch.object(oneshot, "RUNS", tmp_path / "oneshot"), \
         patch.object(oneshot, "PROFILES", tmp_path / "oneshot" / "profiles"), \
         patch.object(connect, "RECORDS", tmp_path / "connect"), \
         patch.object(channels, "WORKSPACE", workspace), \
         patch.object(oneshot, "gh", github.gh), patch.object(oneshot, "remote_id", github.remote_id), \
         patch.object(oneshot, "gh_user", lambda: "someone"), \
         patch.object(oneshot, "url_of", lambda run: str(github.bare)), \
         patch.object(oneshot, "codex_role", lambda: False), \
         patch.object(oneshot, "launch",
                      lambda rid, work, *a, **f: oneshot.changed(rid, **f, working=work, stopped=None)):
        yield {"workspace": workspace, "github": github, "hub": hub, "tmp": tmp_path}


def made(world, **extra) -> str:
    rid = "0123456789abcdef"
    oneshot.save({"id": rid, "version": 1, "created": 0, "state": "bootstrap", "working": "bootstrap", "stopped": None,
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
               {"backend": {"value": "node", "why": "-"}},          # in the schema, not enabled
               {"visibility": {"value": "public", "why": "-"}},     # the person's to decide
               {"budget_usd": {"value": 10, "why": "-"}},
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
    errors, _ = oneshot.problems({**PROFILE, "budget_usd": 0})
    assert any("budget_usd" in e for e in errors)
    errors, _ = oneshot.problems({**PROFILE, "frontend": "tauri"})
    assert any("아직 고를 수 없다" in e for e in errors)
    _, warnings = oneshot.problems({**PROFILE, "python_env": "venv", "os_later": "other"})
    assert any("uv" in w for w in warnings)
    with patch.object(oneshot_profile, "conda_exe", lambda: ""):
        errors, _ = oneshot.problems({**PROFILE, "python_env": "conda", "conda_env": ""})
    assert any("환경 이름" in e for e in errors) and any("conda" in e for e in errors)


def test_gate_cmd_comes_from_the_profile_never_from_guess():
    assert oneshot.gate_cmd(PROFILE) == "uv run pytest"
    assert oneshot.gate_cmd({**PROFILE, "python_env": "venv", "os_now": "windows"}) == ".venv/Scripts/python -m pytest"
    assert oneshot.gate_cmd({**PROFILE, "python_env": "venv", "os_now": "macos"}) == ".venv/bin/python -m pytest"
    with patch.object(oneshot_profile, "conda_exe", lambda: "C:/conda/Scripts/conda.exe"):
        assert oneshot.gate_cmd({**PROFILE, "python_env": "conda", "conda_env": "tracker"}) == \
            '"C:/conda/Scripts/conda.exe" run --no-capture-output -n tracker python -m pytest'


# -- The ledger -------------------------------------------------------------------------

def test_ledger_charges_estimates_meters_codex_by_hours_and_refuses_past_the_ceiling():
    spend = oneshot.ledger(None)
    assert oneshot.allowed(spend, PROFILE, "builder", False) == ""
    with patch.object(oneshot_profile, "estimate", lambda role: 2.0):
        spend = oneshot.charged(spend, "builder", False, None, 10)        # unreported: the estimate, marked unknown
        assert spend["usd"] == 2.0 and spend["unknown"] == 1
        spend = oneshot.charged(spend, "builder", False, 30.0, 10)
        assert spend["usd"] == 32.0 and spend["max"]["builder"] == 30.0
        assert "금액 한도" in oneshot.allowed(spend, PROFILE, "builder", False)   # 32 + 30 > 50
        assert oneshot.allowed(spend, PROFILE, "critic", False) == ""           # 32 + 2 (estimate) <= 50
        for _ in range(3):
            spend = oneshot.charged(spend, "reviewer", True, None, 100)       # Codex: no USD invented
        assert spend["usd"] == 32.0 and spend["calls"] == 5
        assert oneshot.allowed(spend, PROFILE, "reviewer", True) == ""
        spend = oneshot.charged(spend, "reviewer", True, None, 10 * 3600)
        assert "시간 한도" in oneshot.allowed(spend, PROFILE, "reviewer", True)
    restored = json.loads(json.dumps(spend))
    assert oneshot.ledger(restored) == spend


# -- Intake and the card ------------------------------------------------------------------

def test_intake_restates_and_a_refused_block_is_asked_again_once(world):
    replies = [fenced("oneshot-intent", {**INTENT, "kind": "game"}), fenced("oneshot-intent", INTENT)]
    heard = []

    def ask(rid, role, system, text):
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
    (oneshot.PROFILES / "webapp.json").write_text(json.dumps({"styling": "css", "visibility": "public"}), "utf-8")
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-intent", INTENT), {}, False)):
        rid = oneshot.start(oneshot.Start(sentence="가계부"))["id"]
        oneshot.intake(rid)
    oneshot.intent(rid, oneshot.Intent(confirm=True))
    with patch.object(oneshot, "ask", lambda *a: (fenced("oneshot-profile", {"llm": {"value": "anthropic",
                                                                                     "why": "요약"}}), {}, False)):
        oneshot.environment(rid)
    run = oneshot.load(rid)
    assert run["profile"]["styling"] == "css" and run["profile"]["llm"] == "anthropic"
    assert run["profile"]["github_owner"] == "someone"
    with pytest.raises(HTTPException):
        oneshot.profile(rid, oneshot.Profile(values={}, confirm=True))       # budget and docs language empty
    with pytest.raises(HTTPException):
        oneshot.profile(rid, oneshot.Profile(values={"colour": "red"}))
    shown = oneshot.profile(rid, oneshot.Profile(values={"budget_usd": 30, "budget_hours": 5, "docs_language": "ko",
                                                         "license": "mit"}, confirm=True))
    assert shown["state"] == "bootstrap" and shown["confirmed"]


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
    assert 'gate_cmd = "uv run pytest"' in (repo / ".wiki/adapter.toml").read_text(encoding="utf-8")
    spec = specs.load("budget-tracker", oneshot.BRANCH)
    assert spec["oneshot"]["run"] == rid and spec["workspace_mode"] == "worktree"
    assert Path(spec["worktree"]).is_dir() and spec["done"] == ["uv run pytest"]
    assert json.loads((connect.RECORDS / "budget-tracker.json").read_text(encoding="utf-8"))["hash"]
    assert json.loads((oneshot.PROFILES / "webapp.json").read_text(encoding="utf-8"))["budget_usd"] == 50
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
    assert "시간 한도" in oneshot.allowed(spend, {**PROFILE, "budget_hours": 0.5}, "intake", True)


def test_a_running_turn_writes_heartbeats(world):
    rid = made(world, inflight={"role": "intake", "codex": False, "sent": 1.0})
    ended = threading.Event()
    with patch.object(oneshot, "HEARTBEAT", 0.05):
        beat = threading.Thread(target=oneshot.heartbeat, args=(rid, ended))
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
