"""Cloud-to-local verification with real Git, commands and a local HTTP API.

GitHub and the reviewer are stand-ins; no production credentials or datasets
are used. The assertions exercise dispatch and merge boundaries, not just parsers.
"""

import json
import re
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from main import loop, specs, verification
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
    with patch.object(specs, "sh", hub):
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


def test_cloud_runs_real_api_and_independent_review_without_a_local_writer(cloud_world):
    world = cloud_world
    cloud_spec(world)
    spec = looped("cloud")
    assert spec["state"] == "머지 가능"
    record = spec["local_verification"]
    assert record["state"] == "verified" and record["flows"][0]["ok"]
    assert record["flows"][0]["evidence"]["requests"][0]["status"] == 200
    assert record["published"]["state"] == "success"
    assert not Worker.made and Reviewer.made[0].tools == loop.REVIEW_TOOLS
    instruction = (world.tmp / "review/proj/1/round-1.md").read_text(encoding="utf-8")
    assert "local verification" in instruction and "private-api-key" not in instruction
    assert "private-api-key" not in json.dumps(spec) and "private-api-key" not in str(world.hub.comments)
    assert world.github.statuses[-1][1] == "success"
    assert specs.view(world.repo, spec)["unproven"] == ""


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


def test_doc_only_cloud_change_keeps_review_but_exempts_runtime_setup(cloud_world):
    world = cloud_world
    manifest = {**world.contract, "prose_paths": ["docs/*"]}
    (world.repo / "verification.json").write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "prose-policy.txt")
    git(world.repo, "push", "origin", "main")
    cloud_spec(world, file="docs/guide.md", review_profile="plan", artifact_root="docs")
    (world.repo / verification.LOCAL).unlink()
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


def test_saving_changed_environment_revisions_revokes_the_published_pass_immediately(cloud_world):
    world = cloud_world
    cloud_spec(world)
    assert looped("cloud")["state"] == "머지 가능"
    settings = {**world.settings, "revisions": {**world.settings["revisions"], "dataset": "2"}}
    assert client().put("/api/verification/config?sid=cloud", json=settings).status_code == 200
    record = specs.load("proj", "cloud")["local_verification"]
    assert record["state"] == "waiting_review" and record["published"]["state"] == "pending"
    assert world.github.statuses[-1][1] == "pending" and len(Reviewer.made) == 1 and not loop._loops


def test_review_controls_in_real_browser(cloud_world):
    """Built UI drives real setup/resume endpoints and the local HTTP check.

    Optional developer check: use installed Playwright and a built web/dist.
    Only unrelated channel/model listings are replaced in the browser.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    if not (main_app.DIST / "index.html").is_file():
        pytest.skip("Build web before the browser check")
    world = cloud_world
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

            def stub(pattern, value):
                page.route(pattern, lambda route: route.fulfill(json=value))

            stub("**/api/channels", [{"id": name, "label": name, "blurb": "", "live": False, "repo": "proj",
                                       "model": "codex:test", "model_name": "test", "effort": "high", "remote": ""}
                                      for name in ("wiki", "next", "retro")])
            stub("**/api/options", {"projects": [{"id": "proj", "path": str(world.repo), "state": "연결 완료"}],
                                     "models": [], "efforts": [], "codex_error": ""})
            stub("**/api/log/**", [])
            stub("**/api/worktrees", {"project": "proj", "repo": str(world.repo), "rows": [
                {"path": original["worktree"], "name": "cloud", "branch": "cloud", "dirty": False,
                 "merged": False, "live": False, "busy": False}]})
            stub("**/api/work/log?*", {"rows": [], "session_id": "", "busy": False, "running": None,
                                       "rules": [], "queued": None})
            page.goto(address)
            page.get_by_role("button", name=re.compile(r"리뷰 루프 \(1\)")).click()
            page.get_by_role("combobox", name="구현 환경", exact=True).select_option("claude-cloud")
            assert page.get_by_role("combobox", name="구현 환경", exact=True).input_value() == "claude-cloud"
            page.get_by_role("button", name="닫기", exact=True).last.click()
            page.get_by_role("button", name=re.compile(r"cloud.*#1")).click()
            page.get_by_role("tab", name="리뷰", exact=True).click()
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
            playwright.expect(panel.get_by_text("로컬 검증·리뷰 통과", exact=True)).to_be_visible(timeout=30000)
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
        # Close before the fixture unpatches any temporary repository state.
        server.should_exit = True
        thread.join(10)
        listener.close()
