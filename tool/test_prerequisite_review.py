"""Independent prerequisite review precedes implementation completion, never merge."""

# ruff: noqa: F811 -- pytest injects imported shared fixtures by name

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import read_tools
from main import loop, prerequisite_review, specs, verification
from test_loop import (  # noqa: F401 -- isolated real-Git world
    Reviewer, Worker, allow, client, git, no_machine_settings, pr_spec, template, waited, world,
)


REQUEST = {"instructions": "packet.md", "inputs": ["data.json"], "result": "review.json"}


def prepared(world):
    spec = pr_spec(world, "review-before-apply", 11, prerequisite_review=REQUEST,
                   done=["Independent verdict and application are committed"])
    path = Path(spec["worktree"])
    (path / "packet.md").write_text("Read every source row and compare meaning.\n", encoding="utf-8")
    (path / "data.json").write_text('{"value":"before"}\n', encoding="utf-8")
    git(path, "add", "packet.md", "data.json")
    git(path, "commit", "-qm", "review packet")
    git(path, "push", "-q", "origin", spec["id"])
    return spec, path


@pytest.mark.parametrize("field,value", [
    ("instructions", "../outside.md"), ("instructions", ".env"),
    ("inputs", []), ("inputs", ["data.json", "data.json"]),
    ("result", "../review.json"), ("result", "data.json"),
])
def test_request_rejects_unsafe_or_ambiguous_files(field, value):
    with pytest.raises(ValueError):
        prerequisite_review.declared({**REQUEST, field: value})


def test_revision_retains_prerequisite_without_forcing_a_false_done_report(world):
    spec, path = prepared(world)
    specs.update("proj", spec["id"], state="작업 중", pr=None)
    change = {"rev": 1, "reason": "Review must precede application", "goal": spec["goal"],
              "done": spec["done"], "out": [], "prerequisite_review": REQUEST}
    run = SimpleNamespace(put=lambda event: None, chat=SimpleNamespace(id="work", parent_id=None))
    with patch.object(specs, "again") as continuation, patch.object(loop, "kick") as dispatch:
        assert specs._check(path, run, "```spec-update\n" + json.dumps(change) + "\n```") is None
    fresh = specs.load("proj", spec["id"])
    assert fresh["rev"] == 2 and fresh["prerequisite_review"] == REQUEST
    assert not fresh.get("report_asked") and not fresh.get("fault") and fresh["report"] is None
    continuation.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.parametrize("verdict,complete,blocked_acceptance,commit_result", [
    pytest.param("pass", True, False, True, id="complete"),
    pytest.param("fail", False, False, True, id="failed-reading"),
    pytest.param("pass", False, False, True, id="incomplete-work"),
    pytest.param("pass", True, True, True, id="acceptance-still-required"),
    pytest.param("pass", True, False, False, id="uncommitted-verdict"),
])
def test_one_review_request_hands_off_before_full_acceptance_and_preserves_its_gates(
    world, verdict, complete, blocked_acceptance, commit_result,
):
    spec, path = prepared(world)
    if blocked_acceptance:
        specs.update("proj", spec["id"], review={"criteria": ["data"], "evidence": ["api"], "flows": []})
    original_head = git(path, "rev-parse", "HEAD")
    inspected = []

    def review(first, text):
        if "prerequisite-result" not in text:
            assert complete and verdict == "pass"
            assert (path / "review.json").exists() and (path / "application.json").exists()
            assert git(world.origin, "rev-parse", spec["id"]) != original_head
            return allow(first, text)
        identity = re.search(r"Reviewer identity: `([^`]+)`", text)[1]
        assert "prerequisite-result" in Reviewer.made[-1].system
        inspected.append(identity)
        return (first + "\n```prerequisite-result\n" + json.dumps({
            "reviewer_session": identity, "verdict": verdict, "reviewed_rows": ["value"],
        }) + "\n```")

    def finish(work_path, halt):
        assert verdict == "pass" and inspected
        assert not (work_path / "review.json").exists(), "the reviewer never writes source"
        receipt = specs.load("proj", spec["id"])["prerequisite_review_result"]
        assert receipt["reviewer"]["cell"] == inspected[0]
        if not complete:
            return "Production is still blocked; no completion report."
        (work_path / "review.json").write_text(json.dumps(receipt["result"]) + "\n", encoding="utf-8")
        (work_path / "application.json").write_text('{"protected_unchanged":true}\n', encoding="utf-8")
        if commit_result:
            git(work_path, "add", "review.json", "application.json")
            git(work_path, "commit", "-qm", "apply independently reviewed packet")
        return ("```test-cleanup\n" + json.dumps({"audited": [], "removed": [], "reason": "Data evidence only"})
                + "\n```\n```done-report\n" + json.dumps([
                    {"item": item, "pass": True, "evidence": "committed and checked"} for item in spec["done"]
                ]) + "\n```")

    Reviewer.replies = [review, review]
    Worker.replies = [finish]
    real_ready = loop.review_contract.ready

    def normal_ready(*args, **kwargs):
        assert inspected and (path / "application.json").exists()
        return "Registered acceptance evidence is unavailable" if blocked_acceptance else real_ready(*args, **kwargs)

    with patch.object(loop.review_contract, "ready", side_effect=normal_ready):
        result = client().post("/api/loops", json={"prs": [11]}).json()["results"]
        assert result == [{"number": 11, "id": spec["id"]}]
        waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert len(inspected) == 1
    assert fresh["goal"] == spec["goal"] and fresh["done"] == spec["done"]
    if not commit_result:
        assert fresh["state"] == "멈춤" and not fresh["rounds"] and not fresh.get("report")
        assert not fresh.get("report_at")
        assert not fresh["prerequisite_review_result"]["completed"]
        assert git(world.origin, "rev-parse", spec["id"]) == original_head
    elif complete and blocked_acceptance:
        assert fresh["state"] == "멈춤" and not fresh["rounds"]
        assert fresh["prerequisite_review_result"]["completed"]
        assert fresh["report"] and not (fresh.get("validation") or {}).get("final")
    elif complete:
        assert fresh["state"] == "머지 가능" and fresh["validation"]["final"]["ok"]
        assert len(fresh["rounds"]) == 1 and fresh["prerequisite_review_result"]["completed"]
        assert fresh["report"] and fresh["report"][0]["pass"]
        assert fresh["report_at"]["head"] == git(path, "rev-parse", "HEAD")
        assert fresh["report_at"]["rev"] == fresh["rev"]
    else:
        assert fresh["state"] == "멈춤" and not fresh["rounds"]
        assert not (fresh.get("validation") or {}).get("final")
        assert git(world.origin, "rev-parse", spec["id"]) == original_head
        assert not fresh.get("report")
        if verdict == "fail":
            assert not Worker.made and not (path / "review.json").exists()
            client().post(f"/api/specs/{spec['id']}/resume", json={}).raise_for_status()
            waited(lambda: not loop._loops)
            assert len(inspected) == 1 and not Worker.made, "a failed input is not silently reviewed again"
        else:
            complete = True
            Worker.replies = [finish]
            client().post(f"/api/specs/{spec['id']}/resume", json={}).raise_for_status()
            waited(lambda: not loop._loops)
            assert specs.load("proj", spec["id"])["state"] == "머지 가능"
            assert len(inspected) == 1, "resume reuses the bound independent verdict"


def test_snapshot_rejects_untracked_sources_and_changes_when_reviewed_input_changes(world):
    spec, path = prepared(world)
    before = prerequisite_review.snapshot(path, spec)
    (path / "data.json").write_text('{"value":"changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        prerequisite_review.snapshot(path, spec)
    git(path, "commit", "-qam", "change reviewed input")
    assert prerequisite_review.snapshot(path, spec) != before
    spec["prerequisite_review"] = {**REQUEST, "instructions": "untracked.md"}
    (path / "untracked.md").write_text("Do not trust unpublished input.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        prerequisite_review.snapshot(path, spec)


def test_a_hidden_uncommitted_input_is_not_committed(world):
    spec, path = prepared(world)
    git(path, "update-index", "--skip-worktree", "data.json")
    (path / "data.json").write_text('{"value":"never committed"}\n', encoding="utf-8")
    assert git(path, "status", "--porcelain") == ""
    with pytest.raises(ValueError):
        prerequisite_review.snapshot(path, spec)


@pytest.mark.parametrize("hidden", [False, True], ids=["committed-change", "skip-worktree"])
def test_a_completed_receipt_still_requires_the_retained_verdict(world, hidden):
    spec, path = prepared(world)
    result = {"reviewer_session": "reviewer", "verdict": "pass"}
    spec["prerequisite_review_result"] = {
        "input_signature": verification.sha(prerequisite_review.snapshot(path, spec)),
        "result": result, "completed": True,
    }
    (path / "review.json").write_text(json.dumps(result) + "\n", encoding="utf-8")
    git(path, "add", "review.json")
    git(path, "commit", "-qm", "preserve verdict")
    assert prerequisite_review.advance(None, spec, path, path, "", "") is None
    if hidden:
        (path / "review.json").write_text(json.dumps({**result, "verdict": "fail"}) + "\n", encoding="utf-8")
        git(path, "commit", "-qam", "verdict overwritten")
        git(path, "update-index", "--skip-worktree", "review.json")
        (path / "review.json").write_text(json.dumps(result) + "\n", encoding="utf-8")
    else:
        (path / "review.json").write_text(json.dumps({**result, "verdict": "fail"}) + "\n", encoding="utf-8")
        git(path, "commit", "-qam", "verdict overwritten after completion")
    with pytest.raises(ValueError):
        prerequisite_review.advance(None, spec, path, path, "", "")


@pytest.mark.parametrize("defect", ["wrong-identity", "duplicate-block", "duplicate-key", "moved-input"])
def test_invalid_or_stale_review_never_reaches_the_implementer(world, defect):
    spec, path = prepared(world)

    def review(first, text):
        identity = re.search(r"Reviewer identity: `([^`]+)`", text)[1]
        if defect == "moved-input":
            (path / "data.json").write_text('{"value":"changed while reading"}\n', encoding="utf-8")
            git(path, "commit", "-qam", "concurrent source update")
            git(path, "push", "-q", "origin", spec["id"])
        result = {"reviewer_session": "writer" if defect == "wrong-identity" else identity, "verdict": "pass"}
        payload = json.dumps(result)
        if defect == "duplicate-key":
            payload = payload.replace('"verdict": "pass"', '"verdict": "fail", "verdict": "pass"')
        block = "\n```prerequisite-result\n" + payload + "\n```"
        return first + block * (2 if defect == "duplicate-block" else 1)

    Reviewer.replies = [review]
    client().post("/api/loops", json={"prs": [11]}).raise_for_status()
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "멈춤" and not fresh["rounds"] and not Worker.made
    assert not fresh.get("prerequisite_review_result") and not fresh.get("report")
    assert not (fresh.get("validation") or {}).get("final")


def test_prerequisite_reviews_the_head_the_server_just_published(world):
    spec, path = prepared(world)
    (path / "data.json").write_text('{"value":"committed but not pushed"}\n', encoding="utf-8")
    git(path, "commit", "-qam", "prepare updated review inputs")
    expected = git(path, "rev-parse", "HEAD")

    def review(first, text):
        assert first.endswith(expected[:7])
        assert git(world.origin, "rev-parse", spec["id"]) == expected
        identity = re.search(r"Reviewer identity: `([^`]+)`", text)[1]
        return first + "\n```prerequisite-result\n" + json.dumps({
            "reviewer_session": identity, "verdict": "fail", "findings": ["Missing condition"],
        }) + "\n```"

    Reviewer.replies = [review]
    client().post("/api/loops", json={"prs": [11]}).raise_for_status()
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "멈춤" and not Worker.made
    assert fresh["prerequisite_review_result"]["head"] == expected


DIALOGUE = b"original dialogue\n"
EVIDENCE = [{"path": "local/dialogue.txt", "sha256": hashlib.sha256(DIALOGUE).hexdigest()}]


@pytest.mark.parametrize("evidence", [
    "local/dialogue.txt", [], [{"path": "local/dialogue.txt"}], EVIDENCE * 2,
    [{"path": "../dialogue.txt", "sha256": "0" * 64}], [{"path": "local/key.pem", "sha256": "0" * 64}],
    [{"path": "local/dialogue.txt", "sha256": "A" * 64}], [{"path": "data.json", "sha256": "0" * 64}],
])
def test_evidence_declaration_rejects_unsafe_or_ambiguous_files(evidence):
    with pytest.raises(ValueError):
        prerequisite_review.declared({**REQUEST, "evidence": evidence})


@pytest.mark.parametrize("case", ["read", "changed-after-dispatch", "not-ignored"])
def test_declared_ignored_evidence_is_readable_and_bound_to_its_bytes(world, case):
    spec, path = with_evidence(world, ignored=case != "not-ignored")
    request = {**REQUEST, "evidence": EVIDENCE}
    if case == "not-ignored":   # an untracked file Git would show could enter the PR
        with pytest.raises(ValueError, match="무시"):
            prerequisite_review._present(path, request)
        return
    specs.update("proj", spec["id"], prerequisite_review=request)
    seen = []

    def review(first, text):
        assert "local/dialogue.txt" in text and EVIDENCE[0]["sha256"] in text
        seen.append(read_tools.call(path, "repo_read", {"path": "local/dialogue.txt"}, Reviewer.made[-1].evidence))
        if case == "changed-after-dispatch":
            (path / "local" / "dialogue.txt").write_bytes(b"rewritten while reading\n")
        identity = re.search(r"Reviewer identity: `([^`]+)`", text)[1]
        return first + "\n```prerequisite-result\n" + json.dumps({
            "reviewer_session": identity, "verdict": "fail", "findings": ["stop after reading"],
        }) + "\n```"

    Reviewer.replies = [review]
    client().post("/api/loops", json={"prs": [11]}).raise_for_status()
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "멈춤" and not Worker.made
    assert "local/" not in git(world.origin, "ls-tree", "-r", "--name-only", spec["id"])
    assert "1: original dialogue" in seen[0] and Reviewer.made[-1].evidence == {}
    if case == "changed-after-dispatch":
        assert not fresh.get("prerequisite_review_result")
    else:
        receipt = fresh["prerequisite_review_result"]
        manifest = prerequisite_review.snapshot(path, fresh)
        assert manifest["request"]["evidence"] == EVIDENCE
        assert receipt["input_signature"] == verification.sha(manifest)


def with_evidence(world, ignored=True):
    spec, path = prepared(world)
    if ignored:
        with (path / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\nlocal/\n")
        git(path, "add", ".gitignore")
        git(path, "commit", "-qm", "keep local evidence out of Git")
        git(path, "push", "-q", "origin", spec["id"])
    (path / "local").mkdir()
    (path / "local" / "dialogue.txt").write_bytes(DIALOGUE)
    return spec, path


@pytest.mark.parametrize("history", ["clean", "merged-side-branch", "case-variant"])
def test_any_reachable_commit_of_the_evidence_path_counts_as_leaked(world, history):
    spec, path = with_evidence(world)
    spec["prerequisite_review"] = {**REQUEST, "evidence": EVIDENCE}
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if history == "merged-side-branch":   # added then dropped on a side branch, merged with no evidence
        git(path, "checkout", "-qb", "side")
        git(path, "add", "-f", "local/dialogue.txt")
        git(path, "commit", "-qm", "add evidence")
        git(path, "rm", "-q", "--cached", "local/dialogue.txt")
        git(path, "commit", "-qm", "drop evidence")
        git(path, "checkout", "-q", branch)
        git(path, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    elif history == "case-variant":       # the same file on Windows, committed under another case
        blob = git(path, "hash-object", "-w", "local/dialogue.txt")
        git(path, "update-index", "--add", "--cacheinfo", f"100644,{blob},LOCAL/DIALOGUE.TXT")
        git(path, "commit", "-qm", "uppercase evidence")
        git(path, "rm", "-q", "--cached", "LOCAL/DIALOGUE.TXT")
        git(path, "commit", "-qm", "drop uppercase evidence")
    assert bool(prerequisite_review.leaked(path, spec)) is (history != "clean")


def test_the_server_never_pushes_a_commit_carrying_declared_evidence(world):
    spec, path = with_evidence(world)
    specs.update("proj", spec["id"], prerequisite_review={**REQUEST, "evidence": EVIDENCE})
    git(path, "add", "-f", "local/dialogue.txt")
    git(path, "commit", "-qm", "local evidence before review request")
    client().post("/api/loops", json={"prs": [11]}).raise_for_status()
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    assert fresh["state"] == "멈춤" and not Worker.made and not fresh.get("prerequisite_review_result")
    assert "local/" not in git(world.origin, "ls-tree", "-r", "--name-only", spec["id"])


def test_every_server_push_of_commits_goes_through_the_evidence_check():
    """A new push that skips `prerequisite_review.push` fails here; deleting a remote branch uploads nothing."""
    main = Path(prerequisite_review.__file__).parent
    raw = [f"{source.name}:{n}" for source in sorted(main.glob("*.py"))
           for n, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"""["']push["']""", line) and "stash" not in line
           and "prerequisite_review.push(" not in line and "--force-with-lease=refs/heads/{branch}:{approved}" not in line]
    assert raw == []


def test_merge_preparation_never_pushes_leaked_evidence(world):
    from main import maintenance

    spec, path = with_evidence(world)
    spec["prerequisite_review"] = {**REQUEST, "evidence": EVIDENCE}
    published = git(world.origin, "rev-parse", spec["id"])
    git(path, "add", "-f", "local/dialogue.txt")
    git(path, "commit", "-qm", "preparation commit carrying evidence")
    with pytest.raises(ValueError, match="로컬 증거"):
        maintenance.publish(path, spec)
    assert git(world.origin, "rev-parse", spec["id"]) == published


@pytest.mark.parametrize("leak", [False, True], ids=["kept-local", "force-committed"])
def test_evidence_committed_after_a_passing_prerequisite_never_reaches_merge(world, leak):
    spec, path = with_evidence(world)
    specs.update("proj", spec["id"], prerequisite_review={**REQUEST, "evidence": EVIDENCE})

    def review(first, text):
        if "prerequisite-result" not in text:
            return allow(first, text)
        identity = re.search(r"Reviewer identity: `([^`]+)`", text)[1]
        return first + "\n```prerequisite-result\n" + json.dumps(
            {"reviewer_session": identity, "verdict": "pass"}) + "\n```"

    def finish(work_path, halt):
        receipt = specs.load("proj", spec["id"])["prerequisite_review_result"]
        (work_path / "review.json").write_text(json.dumps(receipt["result"]) + "\n", encoding="utf-8")
        git(work_path, "add", "review.json", *(["-f", "local/dialogue.txt"] if leak else []))
        git(work_path, "commit", "-qm", "preserve the independent verdict")
        return ("```test-cleanup\n" + json.dumps({"audited": [], "removed": [], "reason": "Data evidence only"})
                + "\n```\n```done-report\n" + json.dumps([
                    {"item": item, "pass": True, "evidence": "committed"} for item in spec["done"]]) + "\n```")

    Reviewer.replies = [review, review]
    Worker.replies = [finish]
    client().post("/api/loops", json={"prs": [11]}).raise_for_status()
    waited(lambda: not loop._loops)
    fresh = specs.load("proj", spec["id"])
    if leak:
        assert fresh["state"] == "멈춤" and not fresh["prerequisite_review_result"]["completed"]
        assert not (fresh.get("validation") or {}).get("final")
    else:
        assert fresh["state"] == "머지 가능" and fresh["prerequisite_review_result"]["completed"]
    assert "local/" not in git(world.origin, "ls-tree", "-r", "--name-only", spec["id"])
