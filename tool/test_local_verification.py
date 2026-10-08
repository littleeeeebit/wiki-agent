"""Real Git/API review boundaries; GitHub and Jev are isolated stand-ins."""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
import decision
from agent.chat_session import Event
from common import process
from fixtures.review_decision import normalized, transport
from main import decisions, loop, specs, verification
from main import app as main_app
from test_loop import (  # noqa: F401 — shared temporary Git/GitHub fixtures
    Reviewer, Worker, client, commit, git, looped, no_machine_settings,
    deny, pr_spec, template, waited, world as git_world,
)


class GitHub:
    def __init__(self, world):
        self.world, self.statuses = world, []
        self.rule = {"required_status_checks": {"strict": True, "contexts": [verification.CONTEXT, "existing-check"]},
                     "enforce_admins": {"enabled": True}, "required_pull_request_reviews": {"require_code_owner_reviews": True}}

    def __call__(self, args, cwd, timeout=60):
        if args[:2] != ["gh", "api"] or args[2] == "graphql":
            return self.world.hub(args, cwd, timeout)
        endpoint = args[2]
        data = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8")) if "--input" in args else None
        if "/statuses/" in endpoint:
            head = endpoint.rsplit("/", 1)[1]
            self.statuses.append((head, data["state"], data["description"]))
            answer = {"id": len(self.statuses)}
        elif "/pulls/" in endpoint:
            answer = {"head": {"sha": self.world.hub.head(int(endpoint.rsplit("/", 1)[1]))}}
        elif endpoint.endswith("/required_status_checks"):
            self.rule["required_status_checks"] = data
            answer = data
        elif endpoint.endswith("/enforce_admins"):
            self.rule["enforce_admins"] = {"enabled": True}
            answer = self.rule["enforce_admins"]
        elif endpoint.endswith("/protection"):
            answer = self.rule
        elif "/branches/" in endpoint:
            answer = {"protected": True}
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(answer), "")


RUNNER = '''import json, os, sys, urllib.request
from pathlib import Path
values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line)
name = sys.argv[1]
url = values['API_ORIGIN'] + '/health'
with urllib.request.urlopen(url) as response:
    code, payload = response.status, json.load(response)
passed = payload['ok']
receipt = {'head': os.environ['WIKI_VERIFICATION_HEAD'], 'flow': name,
           'environment_id': os.environ['WIKI_VERIFICATION_ENVIRONMENT'], 'test_scope': os.environ['WIKI_VERIFICATION_SCOPE'],
           'observations': [{'id': 'healthy', 'expected': 'API reports healthy', 'actual': str(payload), 'pass': passed}],
           'requests': [{'method': 'GET', 'url': url, 'status': code}]}
if payload.get('missing_dataset'):
    receipt = {k: receipt[k] for k in ('head', 'flow', 'environment_id', 'test_scope')}
    receipt['blocked'] = {'prerequisite': 'dataset', 'reason': 'Dedicated test dataset is not prepared'}
print('SECRET=' + values['SECRET'])
print('```local-evidence')
print(json.dumps(receipt))
print('```')
sys.exit(0 if passed else 1)
'''


@pytest.fixture
def cloud_world(git_world):  # noqa: F811 — pytest injects the imported fixture
    world = git_world
    failed = world.tmp / "api-failure"
    missing = world.tmp / "dataset-missing"

    class API(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": not failed.exists(), "missing_dataset": missing.exists()}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    repo = world.repo
    (repo / ".gitignore").write_text(".env\n.wiki/verification*\n", encoding="utf-8")
    (repo / "api-contract.md").write_text("GET /health returns {ok: boolean}.\n", encoding="utf-8")
    (repo / "verify.py").write_text(RUNNER, encoding="utf-8")
    manifest = {"version": 1, "contracts": ["api-contract.md"], "flows": [
        {"id": "health", "title": "Service health", "kind": "api", "command": "python verify.py health",
         "paths": ["*.py", "api-contract.md", "verification.json"], "environments": ["api", "dataset", "settings"],
         "assertions": [{"id": "healthy", "expected": "API reports healthy"}]}]}
    (repo / "verification.json").write_text(json.dumps(manifest), encoding="utf-8")
    commit(repo, "base.txt")
    git(repo, "push", "origin", "main")
    env = world.tmp / ".env"
    env.write_text(f"API_ORIGIN={origin}\nSECRET=private-api-key\n", encoding="utf-8")
    settings = {"environment_id": "test-api", "test_scope": "dedicated-fixture", "env_file": str(env.resolve()),
                "browser_tool": "project-browser", "allowed_origins": [origin],
                "revisions": {"api": "1", "dataset": "1", "settings": "1"},
                "manifest_digest": verification.manifest(repo)[1]}
    (repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    hub = GitHub(world)
    world.failed, world.missing, world.env, world.settings, world.contract, world.github = failed, missing, env, settings, manifest, hub
    with patch.object(specs, "sh", hub), \
         patch.object(decision, "config", return_value=decision.Config("active", "fixture-jev", "fixture", key="fixture")), \
         patch.object(decisions, "normalized", side_effect=normalized), \
         patch.object(decisions, "transport", side_effect=transport):
        try:
            yield world
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


def cloud_spec(world, name="cloud", n=1, file="change.py", **extra):
    spec = pr_spec(world, name, n, file=file, implementation_environment="claude-cloud", **extra)
    transfer = {"version": 1, "implementation": "claude-code-cloud", "head": world.hub.head(n),
                "summary": "Change the health flow", "run": ["python verify.py health"],
                "checks": ["syntax checked"], "unverified": ["real API and browser"]}
    world.hub.prs[n]["body"] = "```cloud-handoff\n" + json.dumps(transfer) + "\n```"
    return spec


def repair_cloud(world, spec, name="repair.py"):
    head = world.hub.push_elsewhere(spec["pr"]["number"], name)
    body = world.hub.prs[spec["pr"]["number"]]["body"]
    transfer = verification.handoff(body, json.loads(body.split("\n")[1])["head"])
    transfer["head"] = head
    world.hub.prs[spec["pr"]["number"]]["body"] = "```cloud-handoff\n" + json.dumps(transfer) + "\n```"
    return head


@pytest.mark.parametrize("owner", ["local", "external"])
def test_ordinary_api_is_collected_automatically_for_read_only_review(cloud_world, owner):
    world = cloud_world
    spec = pr_spec(world, "automatic-api", 1, "change.py", implementation_environment=owner,
                   review={"flows": ["health"]})
    done = looped(spec["id"])
    assert done["state"] == "머지 가능", done.get("stopped")
    row = done["local_verification"]["flows"][0]
    assert row["ok"] and row["evidence"]["requests"][0]["status"] == 200
    assert Reviewer.made[0].tools == loop.REVIEW_TOOLS and not Worker.made
    assert '"status": 200' in (world.tmp / "review/proj/1/round-1.md").read_text(encoding="utf-8")
    assert not verification.proven(world.repo, Path(spec["worktree"]), done, row["head"],
                                   done["local_verification"]["base_oid"], flow_ids=["health"])
    changed = {**done, "rev": done["rev"] + 1}
    assert verification.proven(world.repo, Path(spec["worktree"]), changed, row["head"],
                               done["local_verification"]["base_oid"], flow_ids=["health"])


@pytest.mark.parametrize("owner", ["local", "external"])
def test_ordinary_failure_returns_only_to_its_implementation_owner(cloud_world, owner):
    world = cloud_world
    spec = pr_spec(world, "owner-failure", 1, "change.py", implementation_environment=owner,
                   review={"flows": ["health"]})
    world.failed.touch()
    done = looped(spec["id"])
    assert done["state"] == "멈춤" and not Reviewer.made
    assert bool(Worker.made) is (owner == "local")
    assert done["local_verification"]["failure_attempts"][0]["evidence"][0]["requests"][0]["status"] == 200
    assert not done["local_verification"].get("delivery")
    assert "private-api-key" not in json.dumps(done)


def test_selected_subset_unknown_ids_and_stale_contract_stop_before_setup(cloud_world, monkeypatch):
    world = cloud_world
    world.contract["flows"].append({**world.contract["flows"][0], "id": "unselected",
                                   "command": "python -c \"raise RuntimeError('unselected flow ran')\""})
    (world.repo / verification.MANIFEST).write_text(json.dumps(world.contract), encoding="utf-8")
    commit(world.repo, "selected-contract.txt")
    git(world.repo, "push", "origin", "main")
    world.settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    spec = pr_spec(world, "subset", 1, "change.py", review={"flows": ["health"]})
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    def forbidden(*args, **kwargs):
        pytest.fail("Unapproved flow must not execute setup or commands")
    with monkeypatch.context() as guarded:
        guarded.setattr(specs, "gate", forbidden)
        for ids, digest in ((["unknown"], ""), (["health"], "stale"), (["health", "health"], "")):
            with pytest.raises(ValueError):
                verification.execute(world.repo, spec, path, head, base, threading.Event(),
                                     flow_ids=ids, enforced_digest=digest)
    done = verification.execute(world.repo, spec, path, head, base, threading.Event(), flow_ids=["health"])
    assert [row["id"] for row in done["local_verification"]["flows"]] == ["health"]


@pytest.mark.parametrize("owner", ["local", "external", "claude-cloud"])
def test_setup_cancellation_is_interrupted_in_executor_and_loop(cloud_world, monkeypatch, owner):
    world = cloud_world
    spec = cloud_spec(world, review={"flows": ["health"]}) if owner == "claude-cloud" else pr_spec(
        world, "setup-cancel", 1, "change.py", implementation_environment=owner, review={"flows": ["health"]})
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    world.settings["setup"] = "cancelled setup"
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    original, calls = specs.gate, []

    def cancelled(command, tree, halt, **kwargs):
        if command != "cancelled setup":
            return original(command, tree, halt, **kwargs)
        calls.append(command)
        halt.set()
        return None, "", "사람이 멈춤"

    monkeypatch.setattr(specs, "gate", cancelled)
    saved = verification.execute(world.repo, spec, path, head, base, threading.Event(), flow_ids=["health"])
    assert saved["local_verification"]["state"] == saved["local_verification"]["outcome"] == "interrupted"
    assert not saved["local_verification"].get("flows")
    stopped = looped(spec["id"])
    record = stopped["local_verification"]
    assert record["state"] == record["outcome"] == "interrupted" and len(calls) == 2
    assert not record.get("flows") and not Reviewer.made and not Worker.made


def test_cleanup_failure_keeps_successful_observations_but_holds_readiness(cloud_world):
    world = cloud_world
    spec = pr_spec(world, "cleanup-failure", 1, "change.py", review={"flows": ["health"]})
    world.settings["cleanup"] = 'python -c "raise SystemExit(2)"'
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    with pytest.raises(ValueError, match="정리"):
        verification.execute(world.repo, spec, path, head, base, threading.Event(), flow_ids=["health"])
    saved = specs.load("proj", spec["id"])
    assert saved["local_verification"]["state"] == "waiting_environment"
    assert saved["local_verification"]["flows"][0]["observed_ok"]
    assert verification.proven(world.repo, path, saved, head, base, flow_ids=["health"])


def test_ordinary_instability_requires_and_records_investigation_before_resume(cloud_world):
    world = cloud_world
    spec = pr_spec(world, "ordinary-unstable", 1, "change.py", implementation_environment="external",
                   review={"flows": ["health"]})
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    world.failed.touch()
    failed = verification.execute(world.repo, spec, path, head, base, threading.Event(), flow_ids=["health"])
    world.failed.unlink()
    unstable = verification.execute(world.repo, failed, path, head, base, threading.Event(), flow_ids=["health"])
    assert unstable["local_verification"]["state"] == "unstable"
    specs.update("proj", spec["id"], state="리뷰 대기")
    loop.stop(None, "proj", spec["id"], loop.Why.PREPARATION, "Investigate observed instability")
    with patch.object(loop, "kick"):
        assert client().post(f"/api/specs/{spec['id']}/resume", json={"note": ""}).status_code == 400
        result = client().post(f"/api/specs/{spec['id']}/resume", json={"note": "Isolated fault injection; cause and both observations checked"})
        assert result.status_code == 200
    record = specs.load("proj", spec["id"])["local_verification"]
    assert not record["needs_research"] and record["research_note"] and record["failure_attempts"]
    assert record["research"][-1]["failure_attempts"] == [0, 1]


def test_changed_managed_env_is_rejected_before_any_flow_command(cloud_world, monkeypatch):
    world = cloud_world
    spec = pr_spec(world, "managed-env-drift", 1, "change.py", review={"flows": ["health"]})
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    prepare = verification.prepare
    def changed_copy(repo, tree, settings):
        prepare(repo, tree, settings)
        with (tree / ".env").open("a", encoding="utf-8") as file:
            file.write("UNAPPROVED=changed\n")
    monkeypatch.setattr(verification, "prepare", changed_copy)
    monkeypatch.setattr(specs, "judge", lambda *_args, **_kwargs: pytest.fail("Unapproved environment must not execute"))
    with pytest.raises(ValueError, match=".env"):
        verification.execute(world.repo, spec, path, head, base, threading.Event(), flow_ids=["health"])


def test_identical_user_env_is_not_claimed_or_overwritten_on_later_source_change(cloud_world):
    world = cloud_world
    spec = pr_spec(world, "user-env", 1, "change.py")
    path = Path(spec["worktree"])
    before = world.env.read_bytes()
    (path / ".env").write_bytes(before)
    verification.prepare(world.repo, path, world.settings)
    assert not (path / ".wiki/verification.env.sha256").exists()
    with world.env.open("a", encoding="utf-8") as file:
        file.write("NEW_SETTING=changed\n")
    with pytest.raises(ValueError, match="다른 .env"):
        verification.prepare(world.repo, path, world.settings)
    assert (path / ".env").read_bytes() == before


def test_owned_gate_cancellation_reaps_descendants_and_preserves_unrelated_process(tmp_path):
    script = tmp_path / "tree.py"
    marker = tmp_path / "pid.txt"
    script.write_text("import subprocess, sys, time\n"
                      "from pathlib import Path\n"
                      "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      "Path(sys.argv[1]).write_text(str(p.pid), encoding='utf-8')\n"
                      "time.sleep(60)\n", encoding="utf-8")
    halt = threading.Event()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 **process.background_options())
    result = []
    command = subprocess.list2cmdline([sys.executable, str(script), str(marker)])
    worker = threading.Thread(target=lambda: result.append(specs.gate(command, tmp_path, halt)))
    worker.start()
    try:
        waited(marker.exists)
        halt.set()
        worker.join(10)
        assert not worker.is_alive() and result[0][0] is None and unrelated.poll() is None
        if os.name == "nt":
            done = subprocess.run(["tasklist", "/FI", "PID eq " + marker.read_text(encoding="utf-8"), "/FO", "CSV"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  **process.background_options())
            assert '"' + marker.read_text(encoding="utf-8") + '"' not in done.stdout
    finally:
        halt.set()
        worker.join(10)
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_automatic_browser_collects_actual_save_reload_api_observations(cloud_world):
    pytest.importorskip("playwright.sync_api")
    world = cloud_world
    script = Path(__file__).with_name("fixtures") / "browser_save.py"
    (world.repo / "verify_browser.py").write_bytes(script.read_bytes())
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    manifest = {"version": 1, "contracts": ["api-contract.md"], "flows": [
        {"id": "save-reload", "title": "Save and reload", "kind": "browser", "command": "python verify_browser.py",
         "paths": ["*.py"], "assertions": [{"id": "persisted", "expected": "Saved value survives reload"}]}]}
    (world.repo / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "browser-contract.txt")
    git(world.repo, "push", "origin", "main")
    with world.env.open("a", encoding="utf-8") as file:
        file.write(f"BROWSER_PORT={port}\n")
    settings = {**world.settings, "allowed_origins": [f"http://127.0.0.1:{port}"],
                "manifest_digest": verification.manifest(world.repo)[1]}
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    spec = pr_spec(world, "automatic-browser", 1, "change.py", review={"flows": ["save-reload"]})
    done = looped(spec["id"])
    assert done["state"] == "머지 가능", done.get("stopped")
    evidence = done["local_verification"]["flows"][0]["evidence"]
    assert evidence["observations"][0]["actual"] == "saved-fixture" and evidence["actions"]
    assert any(row["method"] == "POST" and row["status"] == 201 for row in evidence["requests"])
    assert sum(row["method"] == "GET" and row["status"] == 200 for row in evidence["requests"]) >= 2
    assert Reviewer.made[0].tools == loop.REVIEW_TOOLS


def test_cloud_runs_real_api_and_independent_review_without_a_local_writer(cloud_world):
    world = cloud_world
    cloud_spec(world)
    say = Reviewer.say

    def private_progress(chat, text, halt=None):
        yield Event("progress", "Checking with private-api-key")
        yield from say(chat, text, halt)

    with patch.object(Reviewer, "say", private_progress):
        spec = looped("cloud")
    assert spec["state"] == "머지 가능"
    record = spec["local_verification"]
    assert record["state"] == "verified" and record["flows"][0]["ok"]
    assert record["flows"][0]["evidence"]["requests"][0]["status"] == 200
    assert record["published"]["state"] == "success"
    assert not Worker.made and Reviewer.made[0].tools == loop.CLOUD_TOOLS
    assert Reviewer.made[0].verification and Reviewer.made[0].rest["env"]["WIKI_VERIFICATION_SCOPE"] == world.settings["test_scope"]
    instruction = (world.tmp / "review/proj/1/round-1.md").read_text(encoding="utf-8")
    assert "local verification" in instruction and "private-api-key" not in instruction
    assert "private-api-key" not in json.dumps(spec) and "private-api-key" not in str(world.hub.comments)
    progress = client().get("/api/specs/cloud/review/log").json()
    assert progress["rows"][0]["steps"] and "private-api-key" not in json.dumps(progress)
    assert world.github.statuses[-1][1] == "success"
    assert specs.view(world.repo, spec)["unproven"] == ""


@pytest.mark.parametrize("change_source", [False, True])
def test_cloud_reviewer_artifacts_survive_next_round_but_source_edits_block_completion(cloud_world, change_source):
    world = cloud_world
    cloud_spec(world)
    say, artifacts = Reviewer.say, []

    def execute(chat, text, halt=None):
        fixture = chat.verification / "fixture.py"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("print('CLOUD-FIXTURE-PASS')\n", encoding="utf-8")
        checked = subprocess.run(["python", str(fixture)], cwd=chat.path, capture_output=True, text=True, check=True)
        assert "CLOUD-FIXTURE-PASS" in checked.stdout
        artifacts.append(fixture)
        if change_source:
            (chat.path / "change.py").write_text("unreviewed local implementation\n", encoding="utf-8")
        yield from say(chat, text, halt)

    with patch.object(Reviewer, "say", execute):
        spec = looped("cloud")
        assert artifacts[0].is_file() and not Worker.made
        if change_source:
            assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == loop.Why.PREPARATION
            assert "소스 폴더" in spec["stopped"]["detail"]
            assert not any(row[1] == "success" for row in world.github.statuses)
            assert git(Path(spec["worktree"]), "status", "--porcelain")
        else:
            assert spec["state"] == "머지 가능"
            repair_cloud(world, spec)
            again = looped("cloud")
            assert again["state"] == "머지 가능" and len(again["rounds"]) == 2
            assert len(artifacts) == 2 and artifacts[0] != artifacts[1]
            assert all(file.is_file() for file in artifacts)


def test_cloud_source_changes_during_final_selection_cannot_publish_success(cloud_world):
    world = cloud_world
    original = cloud_spec(world)

    def altered(_loop, _chat, _deferred):
        (Path(original["worktree"]) / "change.py").write_text("unverified change\n", encoding="utf-8")
        return []

    with patch.object(loop, "pick", altered):
        spec = looped("cloud")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == loop.Why.PREPARATION
    assert not any(row[1] == "success" for row in world.github.statuses)
    assert not Worker.made


def test_environment_and_missing_handoff_block_before_dispatch(cloud_world):
    world = cloud_world
    cloud_spec(world)
    (world.repo / verification.LOCAL).unlink()
    spec = looped("cloud")
    assert spec["state"] == "멈춤" and spec["local_verification"]["state"] == "waiting_environment"
    assert not Worker.made and not Reviewer.made
    world.hub.prs[1]["body"] = "no handoff"
    spec = looped("cloud")
    assert "cloud-handoff" in spec["local_verification"]["reason"]
    assert spec["stopped"]["reason"] == "로컬 검증 준비"
    response = client().post("/api/loops", json={"prs": [1], "implementation_environment": "external"})
    assert response.status_code == 200 and "error" not in response.json()["results"][0]
    waited(lambda: ("proj", "cloud") not in loop._loops)
    assert specs.load("proj", "cloud")["implementation_environment"] == "claude-cloud"
    assert not Worker.made and not Reviewer.made
    instructions = client().get("/api/specs/cloud/cloud-instructions").json()["text"]
    assert world.hub.head(1) in instructions and "cloud-handoff" in instructions
    assert "private-api-key" not in instructions
    assert all(s[1] != "success" for s in world.github.statuses)


def test_same_failed_invariant_escalates_after_two_distinct_cloud_repairs(cloud_world):
    world = cloud_world
    cloud_spec(world)
    world.failed.touch()
    spec = looped("cloud")
    assert spec["local_verification"]["state"] == "waiting_cloud" and not Worker.made
    first = spec["local_verification"]["failures"]
    assert len(first["health/healthy"]) == 1
    looped("cloud")
    assert len(specs.load("proj", "cloud")["local_verification"]["failures"]["health/healthy"]) == 1
    for name in ("repair-one.py", "repair-two.py"):
        repair_cloud(world, spec, name)
        spec = looped("cloud")
    assert spec["local_verification"]["state"] == "reanalysis"
    assert len(spec["local_verification"]["failures"]["health/healthy"]) == 3
    assert client().post("/api/specs/cloud/resume", json={}).status_code == 400
    assert not Worker.made and not Reviewer.made


def test_failure_then_pass_on_same_identity_is_unstable(cloud_world):
    world = cloud_world
    cloud_spec(world)
    world.failed.touch()
    looped("cloud")
    world.failed.unlink()
    spec = looped("cloud")
    assert spec["local_verification"]["state"] == "unstable" and spec["state"] == "멈춤"
    row = spec["local_verification"]["flows"][0]
    assert row["attempts"][0]["evidence"]["observations"][0]["pass"] is False
    assert row["evidence"]["observations"][0]["pass"] is True
    assert not Reviewer.made and world.github.statuses[-1][1] == "pending"


def test_verified_environment_change_blocks_merge_and_refreshes_managed_env(cloud_world):
    world = cloud_world
    cloud_spec(world)
    spec = looped("cloud")
    world.env.write_text(world.env.read_text(encoding="utf-8").replace("private-api-key", "rotated-api-key"), encoding="utf-8")
    response = client().post("/api/specs/cloud/merge", json={"head": spec["pr"]["head"]})
    assert response.status_code == 409 and "환경" in response.json()["detail"]
    assert world.github.statuses[-1][1] == "pending"
    spec = looped("cloud")
    assert spec["state"] == "머지 가능"
    assert "rotated-api-key" in (Path(spec["worktree"]) / ".env").read_text(encoding="utf-8")


def test_unaffected_evidence_is_carried_to_new_head_with_a_reason(cloud_world):
    world = cloud_world
    flow = verification.manifest(world.repo)[0].flows[0]
    # A second mapped flow establishes that this path's impact is known.
    manifest = world.contract
    manifest["flows"].append({**manifest["flows"][0], "id": "other", "title": "Other API flow",
                               "command": "python verify.py other", "paths": ["other.txt"]})
    (world.repo / "verification.json").write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "setup.txt")
    git(world.repo, "push", "origin", "main")
    world.settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    cloud_spec(world)
    spec = looped("cloud")
    old = spec["local_verification"]["flows"][0]["executed_head"]
    repair_cloud(world, spec, "other.txt")
    spec = looped("cloud")
    rows = {r["id"]: r for r in spec["local_verification"]["flows"]}
    assert rows[flow.id]["executed_head"] == old and "no changed path" in rows[flow.id]["reuse_reason"]
    assert rows["other"]["executed_head"] != old and not rows["other"]["reuse_reason"]


def test_restart_keeps_completed_flows_and_never_replays_automatically(cloud_world):
    world = cloud_world
    spec = cloud_spec(world)
    base = specs.current_merge_base(Path(spec["worktree"]), "main", spec["pr"]["head"])
    spec = verification.execute(world.repo, spec, Path(spec["worktree"]), spec["pr"]["head"], base, threading.Event())
    spec = verification.keep(spec, state="running")
    specs.save(specs.moved(spec, "리뷰 R1"))
    loop.recover()
    interrupted = specs.load("proj", "cloud")
    assert interrupted["state"] == "멈춤" and interrupted["local_verification"]["state"] == "interrupted"
    assert interrupted["local_verification"]["flows"][0]["ok"] and not loop._loops
    resumed = looped("cloud")
    assert resumed["state"] == "머지 가능" and resumed["local_verification"]["flows"][0]["reuse_reason"]


def test_exit_zero_without_observations_cannot_pass(cloud_world):
    world = cloud_world
    spec = cloud_spec(world)
    path = Path(spec["worktree"])
    commit(path, "verify.py", "print('looks good')\n")
    git(path, "push", "origin", "cloud")
    head = world.hub.head(1)
    transfer = json.loads(world.hub.prs[1]["body"].split("\n")[1])
    transfer["head"] = head
    world.hub.prs[1]["body"] = "```cloud-handoff\n" + json.dumps(transfer) + "\n```"
    spec = looped("cloud")
    assert spec["state"] == "멈춤" and not Reviewer.made
    assert "health/evidence" in spec["local_verification"]["failures"]


def test_browser_receipt_requires_actual_actions_api_and_the_checked_build(cloud_world):
    world = cloud_world
    api_flow = verification.manifest(world.repo)[0].flows[0]
    flow = api_flow.model_copy(update={"kind": "browser"})
    head = "a" * 40
    evidence = {"head": head, "flow": flow.id, "environment_id": world.settings["environment_id"],
                "test_scope": world.settings["test_scope"], "build_head": head,
                "browser_tool": world.settings["browser_tool"],
                "observations": [{"id": "healthy", "expected": "API reports healthy", "actual": "healthy rendered", "pass": True}],
                "requests": [{"method": "GET", "url": world.settings["allowed_origins"][0] + "/health", "status": 200}],
                "actions": [{"action": "click refresh", "expected": "healthy badge", "actual": "healthy badge visible"}]}

    def output(data):
        return "```local-evidence\n" + json.dumps(data) + "\n```"

    assert verification.receipt(output(evidence), flow, head, world.settings)["actions"]
    for invalid in ({**evidence, "actions": []}, {**evidence, "requests": []}, {**evidence, "build_head": "b" * 40}):
        with pytest.raises(ValueError):
            verification.receipt(output(invalid), flow, head, world.settings)


def test_github_requirement_is_enforced_and_setup_preserves_existing_rules(cloud_world):
    world = cloud_world
    cloud_spec(world)
    world.github.rule["required_status_checks"]["contexts"] = ["existing-check"]
    spec = looped("cloud")
    assert spec["state"] == "멈춤" and not Reviewer.made
    response = client().post("/api/verification/protection", json={"base": "main"})
    assert response.status_code == 200
    checks = world.github.rule["required_status_checks"]["checks"]
    assert {c["context"] for c in checks} == {"existing-check", verification.CONTEXT}
    assert world.github.rule["required_pull_request_reviews"]["require_code_owner_reviews"]
    assert looped("cloud")["state"] == "머지 가능"


@pytest.mark.parametrize("environment", ["local", "external"])
def test_ordinary_review_with_local_settings_does_not_require_branch_protection(cloud_world, environment):
    world = cloud_world
    spec = pr_spec(world, "ordinary-unprotected", 12)
    specs.update("proj", spec["id"], implementation_environment=environment)
    github = world.github
    calls = []

    def unprotected(args, cwd, timeout=60):
        if args[:2] == ["gh", "api"] and "/branches/" in args[2]:
            calls.append(args[2])
            return subprocess.CompletedProcess(args, 1, "", "gh: Branch not protected (HTTP 404)")
        return github(args, cwd, timeout)

    with patch.object(specs, "sh", unprotected):
        ready = looped(spec["id"])
    assert ready["state"] == "머지 가능", ready
    assert world.github.statuses[-1][1] == "success" and not calls
    assert not any(args[:3] == ["gh", "pr", "merge"] for args in world.hub.calls)


@pytest.mark.parametrize("failure", ["unprotected", "unreadable"])
def test_cloud_protection_preparation_distinguishes_missing_rule_from_api_failure(cloud_world, failure):
    world = cloud_world
    cloud_spec(world)
    github = world.github

    def branch(args, cwd, timeout=60):
        if args[:2] == ["gh", "api"] and "/branches/" in args[2]:
            assert not args[2].endswith("/protection")
            if failure == "unprotected":
                return subprocess.CompletedProcess(args, 0, '{"protected": false}', "")
            return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
        return github(args, cwd, timeout)

    with patch.object(specs, "sh", branch):
        stopped = looped("cloud")
    assert stopped["state"] == "멈춤" and not Reviewer.made
    assert not any(row[1] == "success" for row in world.github.statuses)
    reason = stopped["local_verification"]["reason"]
    assert ("보호 규칙이 없다" if failure == "unprotected" else "GitHub 검증 상태 연결 실패") in reason


@pytest.mark.parametrize("configuration", ["none", "missing_env"])
def test_doc_only_cloud_change_keeps_review_but_exempts_runtime_setup(cloud_world, configuration):
    world = cloud_world
    manifest = {**world.contract, "prose_paths": ["docs/*"]}
    (world.repo / "verification.json").write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "prose-policy.txt")
    git(world.repo, "push", "origin", "main")
    cloud_spec(world, file="docs/guide.md", review_profile="plan", artifact_root="docs")
    if configuration == "none":
        (world.repo / verification.LOCAL).unlink()
    else:
        world.env.unlink()
    spec = looped("cloud")
    assert spec["state"] == "머지 가능" and spec["local_verification"]["document_only"]
    cloud_spec(world, name="instructions", n=2, file=".claude/rules/api.md")
    rule = looped("instructions")
    assert rule["state"] == "멈춤" and rule["local_verification"]["state"] == "waiting_environment"
    assert spec["rounds"][0]["profile"] == "plan" and not Worker.made


def test_unclassified_runtime_markdown_cannot_skip_local_flows(cloud_world):
    world = cloud_world
    spec = cloud_spec(world, file="scripts/runtime-prompt.md")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    assert not verification.documents(path, base, head)
    (world.repo / verification.LOCAL).unlink()
    assert looped("cloud")["local_verification"]["state"] == "waiting_environment"


@pytest.mark.parametrize("failure", ["final", "round", "review"])
def test_offline_and_review_same_head_recovery_requires_investigation(cloud_world, monkeypatch, failure):
    world = cloud_world
    cloud_spec(world, gate={})
    calls = []
    judge = specs.judge

    def fail_once(path, commands, *args, **kwargs):
        verdict = judge(path, commands, *args, **kwargs)
        if commands != ["python verify.py health"]:
            calls.append(commands)
            if len(calls) == (2 if failure == "final" else 1):
                return {**verdict, "ok": False, "code": 1, "reason": "Intermittent gate", "tail": "First failed result"}
        return verdict

    if failure == "review":
        Reviewer.replies = [deny("[P1] change.py:1 — Intermittent independent finding")]
    else:
        monkeypatch.setattr(specs, "judge", fail_once)
    first = looped("cloud")
    assert first["local_verification"]["state"] == "waiting_cloud"
    retried = looped("cloud")
    record = retried["local_verification"]
    assert record["state"] == "unstable" and record["needs_research"]
    assert retried["state"] == "멈춤" and world.github.statuses[-1][1] == "pending"
    assert record["failure_attempts"][0]["reason"]
    assert client().post("/api/specs/cloud/resume", json={}).status_code == 400
    assert client().post("/api/specs/cloud/resume", json={"note": "Investigated nondeterminism; isolated and measured fixture"}).status_code == 200
    waited(lambda: ("proj", "cloud") not in loop._loops)
    assert specs.load("proj", "cloud")["state"] == "머지 가능"


def test_setup_rejects_stale_manifest_and_requests_from_another_project(cloud_world):
    world = cloud_world
    cloud_spec(world)
    web = client()
    good = web.get("/api/verification/config?sid=cloud").json()
    assert good["manifest_digest"] == world.settings["manifest_digest"]
    response = web.put("/api/verification/config?sid=cloud", json={**world.settings, "manifest_digest": "0" * 64})
    assert response.status_code == 400
    response = web.put("/api/verification/config?sid=cloud", json=world.settings, headers={"X-Project": "another"})
    assert response.status_code == 409
    assert web.put("/api/verification/config?sid=cloud", json=world.settings).status_code == 200


def test_redaction_preserves_json_types_and_handles_escaped_secrets(cloud_world):
    world = cloud_world
    world.env.write_text('SECRET=first-secret\nSECRET=\'a"b\\c\' # local comment\nNUMBER=200\n', encoding="utf-8")
    data = {"text": 'a"b\\c', "status": 200, "ok": True}
    cleaned = verification.sanitize(data, world.settings)
    assert cleaned == {"text": "[redacted]", "status": 200, "ok": True}
    assert "first-secret" not in verification.redact("first-secret", world.settings)
    assert 'a\\"b\\\\c' not in verification.redact(json.dumps(data), world.settings)


def test_missing_test_dataset_waits_without_a_cloud_failure_cycle(cloud_world):
    world = cloud_world
    cloud_spec(world)
    world.missing.touch()
    record = looped("cloud")["local_verification"]
    assert record["state"] == "waiting_environment" and "dataset" in record["reason"]
    assert not record.get("failures") and not world.hub.comments and not Reviewer.made
    world.missing.unlink()
    assert looped("cloud")["state"] == "머지 가능"


def test_distinct_same_head_failures_return_to_cloud_without_duplicate_retries(cloud_world):
    world = cloud_world
    spec = cloud_spec(world)
    head = spec["pr"]["head"]
    spec = verification.return_to_cloud(world.repo, spec, head, "Round gate failed", ["offline/round"])
    spec = verification.return_to_cloud(world.repo, spec, head, "Review found an API defect: private-api-key", ["review/F2"])
    assert len(world.hub.comments) == 2
    assert "Round gate failed" in world.hub.comments[0][1]
    assert "API defect" in world.hub.comments[1][1] and "review/F2" in world.hub.comments[1][1]
    assert "private-api-key" not in world.hub.comments[1][1]
    spec = verification.return_to_cloud(world.repo, spec, head, "Review found an API defect: private-api-key", ["review/F2"])
    assert len(world.hub.comments) == 2 and len(spec["local_verification"]["failure_attempts"]) == 3


def test_final_failure_survives_github_read_outage_and_same_head_retry(cloud_world, monkeypatch):
    world = cloud_world
    cloud_spec(world)
    judge, github = specs.judge, verification.github
    failed = False
    outage = False

    def fail_final(path, commands, *args, **kwargs):
        nonlocal failed, outage
        verdict = judge(path, commands, *args, **kwargs)
        if commands != ["python verify.py health"] and not failed:
            failed, outage = True, True
            return {**verdict, "ok": False, "code": 1, "reason": "Intermittent final failure", "tail": "Recorded first failure"}
        return verdict

    def unavailable(repo, endpoint, *args, **kwargs):
        if outage and "/pulls/" in endpoint:
            raise RuntimeError("network down")
        return github(repo, endpoint, *args, **kwargs)

    monkeypatch.setattr(specs, "judge", fail_final)
    monkeypatch.setattr(verification, "github", unavailable)
    first = looped("cloud")
    record = first["local_verification"]
    assert record["failure_attempts"][0]["reason"].endswith("Recorded first failure")
    assert record["state"] == "waiting_environment" and not record.get("failures")
    outage = False
    retried = looped("cloud")
    record = retried["local_verification"]
    assert record["state"] == "unstable" and record["needs_research"]
    assert retried["state"] == "멈춤" and world.github.statuses[-1][1] == "pending"
    assert len(record["failures"]["offline/final"]) == 1
    assert any("Recorded first failure" in body for _, body in world.hub.comments)


@pytest.mark.parametrize("failure_mode", ["exit", "timeout"])
def test_failed_cloud_comment_is_pending_and_retried_before_verification(cloud_world, monkeypatch, failure_mode):
    world = cloud_world
    cloud_spec(world)
    judge, sh = specs.judge, specs.sh
    failed = False
    outage = True

    def fail_final(path, commands, *args, **kwargs):
        nonlocal failed
        verdict = judge(path, commands, *args, **kwargs)
        if commands != ["python verify.py health"] and not failed:
            failed = True
            return {**verdict, "ok": False, "code": 1, "reason": "First final failure", "tail": "Exact preserved handoff"}
        return verdict

    def unavailable(args, cwd, *rest, **kwargs):
        if outage and args[:3] == ["gh", "pr", "comment"]:
            if failure_mode == "timeout":
                raise subprocess.TimeoutExpired(args[:4], 60)
            return subprocess.CompletedProcess(args, 1, "", "GitHub write unavailable")
        return sh(args, cwd, *rest, **kwargs)

    monkeypatch.setattr(specs, "judge", fail_final)
    monkeypatch.setattr(specs, "sh", unavailable)
    first = looped("cloud")
    record = first["local_verification"]
    assert record["state"] == "waiting_environment" and record["delivery"]["body"]
    assert not world.hub.comments and not record.get("returned_failure")
    saved_body = record["delivery"]["body"]
    calls = []

    def observed(path, commands, *args, **kwargs):
        calls.append(commands)
        assert world.hub.comments and world.hub.comments[0][1] == saved_body
        return judge(path, commands, *args, **kwargs)

    monkeypatch.setattr(specs, "judge", observed)
    assert looped("cloud")["local_verification"]["state"] == "waiting_environment"
    assert not calls and not world.hub.comments
    outage = False
    resumed = looped("cloud")
    assert world.hub.comments[0][1] == saved_body and calls
    assert resumed["local_verification"]["state"] == "unstable"
    assert not resumed["local_verification"].get("delivery")


@pytest.mark.parametrize("missing", ["env", "settings", "env_changed", "settings_changed"])
def test_final_failure_survives_missing_environment_until_same_head_investigation(cloud_world, monkeypatch, missing):
    world = cloud_world
    cloud_spec(world)
    judge = specs.judge
    removed = world.env if missing.startswith("env") else world.repo / verification.LOCAL
    original = removed.read_bytes()
    failed = False

    def fail_final(path, commands, *args, **kwargs):
        nonlocal failed
        verdict = judge(path, commands, *args, **kwargs)
        if commands != ["python verify.py health"] and not failed:
            failed = True
            if missing == "env_changed":
                removed.write_bytes(original.replace(b"private-api-key", b"rotated-api-key"))
            elif missing == "settings_changed":
                changed = json.loads(original)
                changed["revisions"]["dataset"] = "changed-after-observation"
                removed.write_text(json.dumps(changed), encoding="utf-8")
            else:
                removed.unlink()
            return {**verdict, "ok": False, "code": 1, "reason": "First final failure",
                    "tail": "Retain this observation; private-api-key"}
        return verdict

    monkeypatch.setattr(specs, "judge", fail_final)
    first = looped("cloud")
    record = first["local_verification"]
    assert record["state"] == "waiting_environment" and not record.get("failures")
    assert record["failure_attempts"][0]["environment_digest"] is None
    assert "Retain this observation" in record["failure_attempts"][0]["reason"]
    assert "private-api-key" not in json.dumps(record)
    removed.write_bytes(original)
    retried = looped("cloud")
    assert retried["local_verification"]["state"] == "unstable"
    assert retried["local_verification"]["needs_research"]
    assert world.github.statuses[-1][1] == "pending"
    assert any("Retain this observation" in body for _, body in world.hub.comments)
    assert "private-api-key" not in str(world.hub.comments)


def test_local_configuration_cannot_be_saved_as_a_tracked_file(cloud_world):
    world = cloud_world
    git(world.repo, "add", "-f", verification.LOCAL)
    response = client().put("/api/verification/config", json=world.settings)
    assert response.status_code == 400 and "Git" in response.json()["detail"]


def test_research_gate_survives_an_environment_stop_and_consumes_a_recorded_note(cloud_world):
    world = cloud_world
    spec = cloud_spec(world)
    verification.keep(spec, state="waiting_environment", needs_research=True, head=spec["pr"]["head"])
    spec = looped("cloud")
    assert spec["state"] == "멈춤" and spec["local_verification"]["needs_research"]
    assert not Reviewer.made
    assert client().post("/api/specs/cloud/resume", json={}).status_code == 400
    reply = client().post("/api/specs/cloud/resume", json={"note": "API isolation checked; retry on dedicated fixture"})
    assert reply.status_code == 200
    waited(lambda: ("proj", "cloud") not in loop._loops)
    record = specs.load("proj", "cloud")["local_verification"]
    assert record["state"] == "verified" and not record["needs_research"] and not record["research_note"]
    assert record["research"][0]["head"] == spec["pr"]["head"]


def test_a_cloud_push_during_execution_cannot_create_a_review_or_failure_cycle(cloud_world, monkeypatch):
    world = cloud_world
    spec = cloud_spec(world)
    judge = specs.judge

    def moved(path, commands, *args, **kwargs):
        verdict = judge(path, commands, *args, **kwargs)
        if commands == ["python verify.py health"]:
            repair_cloud(world, spec)
        return verdict

    monkeypatch.setattr(specs, "judge", moved)
    record = looped("cloud")["local_verification"]
    assert record["state"] == "waiting_environment" and "바뀌었다" in record["reason"]
    assert not record.get("failures") and not Reviewer.made and world.github.statuses[-1][1] == "pending"


@pytest.mark.parametrize("passed", [False, True])
def test_flow_observation_survives_missing_post_command_environment(cloud_world, monkeypatch, passed):
    world = cloud_world
    cloud_spec(world)
    original = world.env.read_bytes()
    judge = specs.judge
    first = True
    if not passed:
        world.failed.touch()

    def disappear(path, commands, *args, **kwargs):
        nonlocal first
        verdict = judge(path, commands, *args, **kwargs)
        if first and commands == ["python verify.py health"]:
            first = False
            world.env.unlink()
        return verdict

    monkeypatch.setattr(specs, "judge", disappear)
    interrupted = looped("cloud")["local_verification"]
    row = interrupted["flows"][0]
    assert interrupted["state"] == "waiting_environment" and not interrupted.get("failures")
    assert row["finished_at"] is not None and row["observed_ok"] is passed
    assert row["evidence"]["observations"][0]["pass"] is passed
    assert "private-api-key" not in json.dumps(interrupted)
    world.env.write_bytes(original)
    world.failed.unlink(missing_ok=True)
    resumed = looped("cloud")["local_verification"]
    assert resumed["state"] == ("verified" if passed else "unstable")
    assert bool(resumed.get("needs_research")) is not passed


@pytest.mark.parametrize("passed", [False, True])
def test_restart_after_observation_preserves_failure_without_inventing_one(cloud_world, monkeypatch, passed):
    world = cloud_world
    spec = cloud_spec(world)
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    if not passed:
        world.failed.touch()
    signature = verification.signature
    calls = 0

    def crash_after_observation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SystemExit("simulated process interruption")
        return signature(*args, **kwargs)

    monkeypatch.setattr(verification, "signature", crash_after_observation)
    with pytest.raises(SystemExit):
        verification.execute(world.repo, spec, path, head, base, threading.Event())
    saved = specs.load("proj", "cloud")
    row = saved["local_verification"]["flows"][0]
    assert row["observed_ok"] is passed and row["blocked"] is passed and not row["ok"]
    assert row["finished_at"] is not None
    monkeypatch.setattr(verification, "signature", signature)
    world.failed.unlink(missing_ok=True)
    resumed = verification.execute(world.repo, saved, path, head, base, threading.Event())
    assert resumed["local_verification"]["state"] == ("runtime_passed" if passed else "unstable")


@pytest.mark.parametrize("kind", ["api", "command"])
def test_preserved_request_metadata_is_validated_for_every_flow(cloud_world, kind):
    world = cloud_world
    flow = verification.manifest(world.repo)[0].flows[0].model_copy(update={"kind": kind})
    head = "a" * 40
    evidence = {"head": head, "flow": flow.id, "environment_id": world.settings["environment_id"],
                "test_scope": world.settings["test_scope"],
                "observations": [{"id": "healthy", "expected": "API reports healthy", "actual": "healthy", "pass": True}],
                "requests": [{"method": "private-api-key", "url": world.settings["allowed_origins"][0] + "/health", "status": 200}]}
    with pytest.raises(ValueError, match="메서드"):
        verification.receipt("```local-evidence\n" + json.dumps(evidence) + "\n```", flow, head, world.settings)
    evidence["requests"] = []
    if kind == "command":
        assert verification.receipt("```local-evidence\n" + json.dumps(evidence) + "\n```", flow, head, world.settings)


def test_common_env_values_do_not_change_public_protocol_metadata(cloud_world):
    world = cloud_world
    world.env.write_text(world.env.read_text(encoding="utf-8") +
                         "FLAG=1\nZERO=0\nDEBUG=true\nCODE=200\nHTTP_METHOD=GET\n", encoding="utf-8")
    cloud_spec(world)
    spec = looped("cloud")
    head = spec["pr"]["head"]
    assert spec["state"] == "머지 가능" and spec["cloud_handoff"]["head"] == head
    evidence = spec["local_verification"]["flows"][0]["evidence"]
    assert evidence["requests"][0]["method"] == "GET" and evidence["requests"][0]["status"] == 200
    assert evidence["observations"][0]["id"] == "healthy" and evidence["observations"][0]["pass"] is True
    body = world.hub.comments[-1][1]
    assert f"`{head}`" in body
    for block in re.findall(r"```json\n(.*?)\n```", body, re.S):
        assert json.loads(block)["observations"][0]["pass"] is True
    verification.return_to_cloud(world.repo, spec, head, "Review failed; private-api-key", ["review/F1"])
    body = world.hub.comments[-1][1]
    assert f"`{head}`" in body and "`review/F1`" in body and "private-api-key" not in body


@pytest.mark.parametrize("failure", ["flow", "round", "final", "review"])
def test_failure_text_stays_private_after_both_env_files_disappear(cloud_world, monkeypatch, failure):
    world = cloud_world
    world.env.write_text(world.env.read_text(encoding="utf-8") + "FLAG=1\nZERO=0\n", encoding="utf-8")
    spec = cloud_spec(world, gate={})
    path = Path(spec["worktree"])
    original = world.env.read_bytes()
    judge = specs.judge
    calls = 0

    def disappear():
        world.env.unlink()
        (path / ".env").unlink()

    def fail_gate(*args, **kwargs):
        nonlocal calls
        verdict = judge(*args, **kwargs)
        if args[1] != ["python verify.py health"] or failure == "flow":
            calls += 1
            if calls == (2 if failure == "final" else 1):
                disappear()
                return {**verdict, "ok": False, "code": 1, "reason": "Retain this observation; private-api-key",
                        "tail": "Retain this observation; private-api-key\n" + verdict["tail"]}
        return verdict

    def fail_review(first, text):
        disappear()
        meta = [{"ordinal": 1, "component": "api route", "invariant": "route stays healthy",
                 "trigger": "Retain this observation; private-api-key", "evidence": "private-api-key",
                 "unknown": {"private-api-key": "private-api-key"}}]
        return deny("[P1] change.py:1 — Retain this observation; private-api-key\n"
                    "```finding-meta\n" + json.dumps(meta) + "\n```")(first, text)

    if failure == "review":
        Reviewer.replies = [fail_review]
    else:
        monkeypatch.setattr(specs, "judge", fail_gate)
    first = looped("cloud")
    assert first["local_verification"]["state"] == "waiting_environment"
    assert "private-api-key" not in json.dumps(first)
    assert "Retain this observation" in json.dumps(first["local_verification"]["failure_attempts"])
    if failure == "review":
        item = first["rounds"][0]["items"][0]
        assert item["head"].startswith("[P1] change.py:1")
        assert (item["id"], item["component"], item["invariant"]) == ("F1", "api route", "route stays healthy")
        report = (loop.folder("proj", first["pr"]["number"]) / "round-1-result.md").read_text(encoding="utf-8")
        assert "private-api-key" not in report
        assert loop.parse(report, 1, first["pr"]["number"], first["pr"]["head"])["findings"][0]["meta"]["component"] == "api route"
    world.env.write_bytes(original)
    retried = looped("cloud")
    assert retried["local_verification"]["state"] == "unstable"
    assert "private-api-key" not in json.dumps(retried) and "private-api-key" not in str(world.hub.comments)


@pytest.mark.parametrize("field", ["component", "invariant", "existing_id"])
def test_private_reviewer_identity_is_rejected_without_losing_the_failure(cloud_world, field):
    world = cloud_world
    world.env.write_text(world.env.read_text(encoding="utf-8") + "FLAG=1\nZERO=0\n", encoding="utf-8")
    spec = cloud_spec(world, gate={})
    if field == "existing_id":
        meta = [{"ordinal": 1, "component": "api route", "invariant": "route stays healthy"}]
        Reviewer.replies = [deny("[P1] change.py:1 — Retain the actual failure\n"
                                 "```finding-meta\n" + json.dumps(meta) + "\n```")]
        spec = looped("cloud")
        assert spec["rounds"][0]["items"][0]["id"] == "F1"
        repair_cloud(world, spec)
    path = Path(spec["worktree"])
    original = world.env.read_bytes()

    def respond(first, text):
        meta = {"ordinal": 1, "component": "api route", "invariant": "route stays healthy",
                "trigger": "private-api-key", "evidence": "private-api-key",
                "unknown": {"private-api-key": "private-api-key"}}
        if field == "existing_id":
            meta.update(existing_id="F1", component="api route private-api-key", invariant="private-api-key")
        else:
            meta[field] += " private-api-key"
        world.env.unlink()
        (path / ".env").unlink()
        file = "private-api-key.py" if field == "component" else "change.py"
        return deny(f"[P1] {file}:1 — Retain the actual failure\n"
                    "```finding-meta\n" + json.dumps([meta]) + "\n```")(first + " — private-api-key", text)

    Reviewer.replies = [respond]
    first = looped("cloud")
    number = 2 if field == "existing_id" else 1
    report = (loop.folder("proj", first["pr"]["number"]) / f"round-{number}-result.md").read_text(encoding="utf-8")
    assert "private-api-key" not in json.dumps(first) and "private-api-key" not in report
    row = first["rounds"][-1]
    identity = "full" if field == "existing_id" else "limited"
    assert report.splitlines()[0] == f"Round {number} · PR #{first['pr']['number']} · {row['head'][:7]}"
    assert row["verdict"] == "deny" and row["findings"]["P1"] == 1
    assert row["items"][0]["grade"] == "P1" and row["items"][0]["line"] == 1
    assert row["identity"] == identity and row["items"][0]["id"] == ("F1" if field == "existing_id" else None)
    assert loop.parse(report, number, first["pr"]["number"], row["head"], {"F1"})["identity"] == identity
    world.env.write_bytes(original)
    prompt = loop.instruction(first, path, number + 1, row["head"], "main", True)
    assert "private-api-key" not in prompt
    resumed = looped("cloud")
    assert resumed["local_verification"]["state"] == "unstable" and resumed["local_verification"]["needs_research"]


def test_mixed_identity_report_preserves_known_recurrence_through_two_repairs(cloud_world):
    world = cloud_world
    cloud_spec(world, gate={})
    clean = {"ordinal": 1, "component": "api route", "invariant": "route stays healthy"}
    Reviewer.replies = [deny("[P1] change.py:1 — Same known failure\n"
                             "```finding-meta\n" + json.dumps([clean]) + "\n```")]
    spec = looped("cloud")
    heads = [spec["rounds"][-1]["head"]]
    results = []
    for name in ("repair-one.py", "repair-two.py"):
        repair_cloud(world, spec, name)
        meta = [{**clean, "existing_id": "F1"},
                {"ordinal": 2, "component": "new private-api-key", "invariant": "another failure"}]
        Reviewer.replies = [deny("[P1] change.py:1 — Same known failure\n"
                                 "[P1] extra.py:2 — Another independent failure\n"
                                 "```finding-meta\n" + json.dumps(meta) + "\n```")]
        spec = looped("cloud")
        row = spec["rounds"][-1]
        heads.append(row["head"])
        report = (loop.folder("proj", spec["pr"]["number"]) / f"round-{row['n']}-result.md").read_text(encoding="utf-8")
        results.append((row, report, loop.instruction(spec, Path(spec["worktree"]), row["n"] + 1, row["head"], "main", True)))
    assert spec["local_verification"]["state"] == "reanalysis"
    assert spec["local_verification"]["failures"]["F1"] == heads and spec["local_verification"]["needs_research"]
    assert "review/change.py:1" not in spec["local_verification"]["failures"]
    assert "private-api-key" not in json.dumps(spec) and "private-api-key" not in str(world.hub.comments)
    for row, report, prompt in results:
        assert row["identity"] == "limited" and [i["id"] for i in row["items"]] == ["F1", None]
        parsed = loop.parse(report, row["n"], spec["pr"]["number"], row["head"], {"F1"})
        assert parsed["identity"] == "limited" and parsed["findings"][0]["meta"]["existing_id"] == "F1"
        assert "meta" not in parsed["findings"][1]
        assert "private-api-key" not in report and "private-api-key" not in prompt and "`F1`" in prompt


def test_passing_round_gate_source_loss_never_reaches_review_prompt(cloud_world, monkeypatch):
    world = cloud_world
    cloud_spec(world, gate={})
    original = world.env.read_bytes()
    judge = specs.judge
    first = True

    def lose_source(path, commands, *args, **kwargs):
        nonlocal first
        verdict = judge(path, commands, *args, **kwargs)
        if first and commands != ["python verify.py health"]:
            first = False
            world.env.unlink()
            return {**verdict, "tail": "Passing gate printed private-api-key"}
        return verdict

    monkeypatch.setattr(specs, "judge", lose_source)
    interrupted = looped("cloud")
    assert interrupted["local_verification"]["state"] == "waiting_environment" and not Reviewer.made
    assert "private-api-key" not in json.dumps(interrupted)
    world.env.write_bytes(original)
    resumed = looped("cloud")
    assert resumed["state"] == "머지 가능"
    order = loop.folder("proj", resumed["pr"]["number"]) / "round-1.md"
    assert "private-api-key" not in order.read_text(encoding="utf-8")


def test_saving_changed_environment_revisions_revokes_the_published_pass_immediately(cloud_world):
    world = cloud_world
    cloud_spec(world)
    assert looped("cloud")["state"] == "머지 가능"
    settings = {**world.settings, "revisions": {**world.settings["revisions"], "dataset": "2"}}
    assert client().put("/api/verification/config?sid=cloud", json=settings).status_code == 200
    record = specs.load("proj", "cloud")["local_verification"]
    assert record["state"] == "waiting_review" and record["published"]["state"] == "pending"
    assert world.github.statuses[-1][1] == "pending" and len(Reviewer.made) == 1 and not loop._loops


def test_review_controls_in_real_browser(cloud_world, monkeypatch):
    """Built UI drives real setup/resume endpoints and the local HTTP check.

    Optional developer check: use installed Playwright and a built web/dist.
    Only unrelated channel/model listings are replaced in the browser.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    if not (main_app.DIST / "index.html").is_file():
        pytest.skip("Build web before the browser check")
    world = cloud_world
    monkeypatch.setattr(loop, "review_model", lambda chosen=None: chosen or loop.settings()["review_model"] or "codex:test")
    monkeypatch.setattr(loop.channels, "codex_models", lambda: [
        {"id": "codex:test", "efforts": [{"id": ""}, {"id": "high"}]},
        {"id": "codex:review", "efforts": [{"id": ""}, {"id": "high"}]}])
    release_review = threading.Event()
    say = Reviewer.say
    make_cell = loop.cell

    def delayed_cell(spec, path):
        # Valid preparation may take longer than Playwright's default five seconds.
        time.sleep(6)
        return make_cell(spec, path)

    monkeypatch.setattr(loop, "cell", delayed_cell)

    def visible_review(chat, text, halt=None):
        yield Event("progress", "Reviewer progress.\nChecking the remote head.")
        assert release_review.wait(15)
        for event in say(chat, text, halt):
            if event.kind == "done":
                yield Event("progress", event.text)
                yield Event("hook", "Stop hook after the final text block")
            yield event

    monkeypatch.setattr(Reviewer, "say", visible_review)
    original = cloud_spec(world)
    (world.repo / verification.LOCAL).unlink()
    assert looped("cloud")["state"] == "멈춤"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(main_app.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    browser = None
    try:
        waited(lambda: server.started)
        with playwright.sync_playwright() as automation:
            try:
                browser = automation.chromium.launch(headless=True)
            except playwright.Error as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Install Playwright Chromium for the browser check")
                raise
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""(() => {
              const callbacks = new Map(), listeners = new Map(); let sequence = 0;
              window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
              window.__TAURI_INTERNALS__ = {
                metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main', windowLabel: 'main' } },
                transformCallback: callback => { callbacks.set(++sequence, callback); return sequence; },
                unregisterCallback: id => callbacks.delete(id),
                invoke: async (command, args) => {
                  if (command === 'plugin:event|listen') { listeners.set(args.event, args.handler); return args.handler; }
                  if (command === 'pty_open') return 1;
                  if (command === 'pty_write') {
                    window.terminalInput = args.data;
                    window.terminalOutput(args.data.includes('\\r')
                      ? '\\r\\nTerminal progress.\\n\\nSecond output line.\\n' : args.data);
                  }
                  return null;
                },
              };
              window.terminalOutput = text => callbacks.get(listeners.get('pty-out'))?.({
                payload: [1, Array.from(new TextEncoder().encode(text))],
              });
            })();""")
            translations = []

            def translate_output(route):
                texts = route.request.post_data_json["texts"]
                translations.extend(texts)
                replacements = {"Agent progress.\nSecond progress line.": "진행상황.\n두 번째 진행 줄.",
                                "Reviewer progress.\nChecking the remote head.": "리뷰 진행상황.\n원격 커밋 확인 중.",
                                "Bash · Checking behavior": "Bash · 동작 확인 중",
                                "Terminal progress.": "터미널 진행상황.", "Second output line.": "두 번째 출력 줄."}
                route.fulfill(json={"texts": [replacements.get(text, text) for text in texts]})

            page.route("**/api/translate", translate_output)

            def stub(pattern, value):
                page.route(pattern, lambda route: route.fulfill(json=value))

            stub("**/api/channels", [{"id": name, "label": name, "blurb": "", "live": False, "repo": "proj",
                                       "model": "codex:test", "model_name": "test", "effort": "high", "remote": ""}
                                      for name in ("wiki", "next", "retro")])
            stub("**/api/options", {"projects": [{"id": "proj", "path": str(world.repo), "state": "연결 완료"}],
                                     "models": [
                                         {"id": "codex:test", "label": "Work test", "note": "", "is_default": True},
                                         {"id": "codex:review", "label": "Review test", "note": ""}],
                                     "efforts": [{"id": "", "label": "기본", "note": ""},
                                                 {"id": "high", "label": "high", "note": ""}], "codex_error": ""})
            stub("**/api/log/**", [])
            stub("**/api/worktrees", {"project": "proj", "repo": str(world.repo), "rows": [
                {"path": original["worktree"], "name": "cloud", "branch": "cloud", "dirty": False,
                 "merged": False, "live": False, "busy": False}]})
            stub("**/api/work/log?*", {"rows": [{"role": "assistant", "text": "Finished.", "steps": [
                {"kind": "progress", "text": "Agent progress.\nSecond progress line."},
                {"kind": "tool", "text": "Bash · Checking behavior · $ git status\ngit diff"}]}],
                                       "session_id": "", "busy": False, "running": None,
                                       "rules": [], "queued": None})
            page.goto(address)
            page.get_by_role("button", name=re.compile(r"리뷰 루프 \(1\)")).click()
            page.get_by_role("combobox", name="구현 환경", exact=True).select_option("claude-cloud")
            assert page.get_by_role("combobox", name="구현 환경", exact=True).input_value() == "claude-cloud"
            page.get_by_role("button", name="닫기", exact=True).last.click()
            page.get_by_role("button", name=re.compile(r"cloud.*#1")).click()
            agent = page.get_by_role("region", name="에이전트 세션")
            playwright.expect(agent.get_by_text("진행상황.\n두 번째 진행 줄.", exact=True)).to_be_visible()
            progress = agent.get_by_text("진행상황.\n두 번째 진행 줄.", exact=True)
            assert progress.evaluate("element => getComputedStyle(element).whiteSpace") == "pre-wrap"
            playwright.expect(agent.get_by_text("· Bash · 동작 확인 중 · $ git status\ngit diff", exact=True)).to_be_visible()
            assert not any("git status" in text for text in translations)
            page.get_by_role("tab", name="터미널", exact=True).click()
            page.locator(".xterm-helper-textarea").focus()
            page.keyboard.insert_text("user-command")
            page.keyboard.press("Enter")
            terminal = page.get_by_role("region", name="터미널 출력 번역")
            page.wait_for_timeout(800)
            assert not any("Terminal progress." in text for text in translations)
            screen = page.locator(".xterm-screen").bounding_box()
            page.mouse.move(screen["x"] + 2, screen["y"] + 2)
            page.mouse.down()
            page.mouse.move(screen["x"] + screen["width"] - 2, screen["y"] + screen["height"] - 2, steps=10)
            page.mouse.up()
            terminal.get_by_role("button", name="선택한 출력 가져오기", exact=True).click()
            preview = terminal.get_by_role("textbox", name="외부 번역 서비스로 보낼 내용")
            assert "Terminal progress." in preview.input_value()
            assert not any("Terminal progress." in text for text in translations)
            preview.fill("Terminal progress.\n\nSecond output line.")
            terminal.get_by_role("button", name="확인한 내용 번역", exact=True).click()
            rendered = terminal.get_by_text("터미널 진행상황.\n\n두 번째 출력 줄.", exact=True)
            playwright.expect(rendered).to_be_visible()
            assert rendered.evaluate("element => getComputedStyle(element).whiteSpace") == "pre-wrap"
            assert not any("user-command" in text for text in translations)
            assert page.evaluate("window.terminalInput") == "\r"
            # A Windows prompt can wrap before its closing `>` or echoed command.
            page.evaluate("window.terminalOutput('PS C:\\\\' + 'long-worktree-'.repeat(12) + '> hidden-command\\r\\n')")
            page.wait_for_timeout(800)
            playwright.expect(rendered).to_be_visible()
            assert not any("hidden-command" in text or "long-worktree" in text for text in translations)
            page.get_by_role("tab", name="리뷰", exact=True).click()
            page.get_by_role("region", name="리뷰 세션").get_by_role("combobox", name="모델", exact=True).click()
            with page.expect_response(lambda r: "/api/loop/settings" in r.url and r.request.method == "POST") as reply:
                page.get_by_role("option", name="Review test", exact=True).click()
            assert reply.value.status == 200 and loop.settings()["review_model"] == "codex:review"
            page.get_by_role("button", name="클라우드 인계 지시 보기", exact=True).click()
            playwright.expect(page.get_by_role("textbox", name="클라우드 구현자에게 전달할 지시")).to_have_value(re.compile("cloud-handoff"))
            page.get_by_role("button", name="프로젝트 검증 설정", exact=True).click()
            panel = page.get_by_role("region", name="클라우드 구현의 로컬 검증")
            for label, value in [("테스트 환경 이름", "test-api"), ("테스트 계정·데이터 범위", "dedicated-fixture"),
                                 ("로컬 .env 파일의 절대 경로", str(world.env.resolve())),
                                 ("브라우저 검증 도구", "project-browser")]:
                panel.get_by_label(label, exact=True).fill(value)
            panel.get_by_label("허용 테스트 API origin · 한 줄에 하나", exact=True).fill(world.settings["allowed_origins"][0])
            for key in ("api", "dataset", "settings"):
                panel.get_by_label(f"{key} 버전 · 환경이 바뀌면 갱신", exact=True).fill("1")
            panel.get_by_text("승인할 주요 흐름과 실행 명령", exact=True).click()
            with page.expect_response(lambda r: "/api/verification/config" in r.url and r.request.method == "PUT") as reply:
                panel.get_by_role("button", name="명령 확인 후 설정 저장", exact=True).click()
            assert reply.value.status == 200 and reply.value.request.headers["x-project"] == "proj"
            assert verification.local(world.repo)["env_file"] == str(world.env.resolve())
            with page.expect_response(lambda r: "/api/verification/protection" in r.url) as reply:
                panel.get_by_role("button", name="GitHub 필수 검사 설정", exact=True).click()
            assert reply.value.status == 200
            with page.expect_response(lambda r: "/api/specs/cloud/resume" in r.url) as reply:
                page.get_by_role("button", name="로컬 검증 재개", exact=True).click()
            assert reply.value.status == 200
            review_progress = page.get_by_role("region", name="리뷰 진행상황")
            # Each wait spans a whole loop drive; like `looped()`, allow 120 s on a busy host.
            playwright.expect(review_progress.get_by_text("리뷰 진행상황.\n원격 커밋 확인 중.", exact=True)).to_be_visible(timeout=120000)
            assert specs.load("proj", "cloud")["state"] == "리뷰 R1"
            release_review.set()
            playwright.expect(panel.get_by_text("로컬 검증·리뷰 통과", exact=True)).to_be_visible(timeout=120000)
            playwright.expect(page.get_by_role("region", name="리뷰 진행상황")).to_be_visible()
            playwright.expect(page.get_by_role("button", name="다음 리뷰 라운드", exact=True)).to_be_visible()
            assert Reviewer.made[-1].model == "codex:review" and not Worker.made
            panel.get_by_text("Service health · 통과", exact=True).click()
            assert panel.get_by_text(re.compile('"status": 200')).count() > 0
            assert not errors and specs.load("proj", "cloud")["state"] == "머지 가능"
            artifacts = main_app.ROOT / "artifacts"
            artifacts.mkdir(exist_ok=True)
            page.screenshot(path=str(artifacts / "local-review-wide.png"))
            page.set_viewport_size({"width": 1024, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(artifacts / "local-review-narrow.png"))
            browser.close()
            browser = None
    finally:
        release_review.set()
        # Close before the fixture unpatches any temporary repository state.
        server.should_exit = True
        thread.join(10)
        listener.close()
