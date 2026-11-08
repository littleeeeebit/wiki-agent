"""A spec's requirements section, its version, and the human and agent views rendered from one snapshot."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from common import settings
from main import query as chat
from main import spec_views, specs, work
from test_main import client, no_machine_settings, settled  # noqa: F401 — the fixture is autouse
from test_specs import Worker, fresh_workers, made, repo as repository_fixture, spec_block  # noqa: F401 — fixtures

repo = repository_fixture
BRIEFS = Path(__file__).parent / "fixtures" / "spec_briefs.json"


def req(rid, status="confirmed", kind="original", **extra) -> dict:
    return {"id": rid, "text": f"{rid} keeps the login page under 2 s and never loses the draft",
            "source": {"kind": kind, "locator": f"request ¶{rid[1:]}"}, "status": status, **extra}


REQS = [req("R1", **{"global": True, "invariants": ["No request leaves the machine"],
                     "verify": [{"item": "pytest tool/test_a.py", "expected": "passes in 30 s"}]}),
        req("R2", status="undecided", kind="decision"),
        req("R3", status="assumed", kind="policy", depends_on=["R2"], context=["tool/a.py"],
            preconditions=["a draft exists"], postconditions=["the draft is not lost"], failures=["a 500 MB upload"],
            verify=[{"item": "open the page", "expected": "shows the draft"}])]


def snapshot(reqs=REQS, **extra) -> dict:
    spec = {"id": "fixture", "rev": 1, "goal": "Keep drafts", "out": ["Sync"], "original": "Keep my drafts.\n\nPlease.",
            "requirements": spec_views.requirements(copy.deepcopy(reqs)), **extra}
    spec_views.stamped(None, spec)
    return spec


@pytest.fixture
def views(monkeypatch, tmp_path):
    """`SPEC_VIEWS=on` in a hub `.env` of its own."""
    monkeypatch.delenv("SPEC_VIEWS", raising=False)
    env = tmp_path / "hub.env"
    env.write_text("SPEC_VIEWS=on\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))


# -- the record ------------------------------------------------------------------


@pytest.mark.parametrize("bad, named", [
    ([{**req("R1"), "owner": "x"}], "R1: 모르는 키 — owner"),
    ([req("R1"), req("R1")], "R1: id 가 겹친다"),
    ([req("R1", depends_on=["R7"])], "R1: `depends_on` 이 정의되지 않은 id 를 가리킨다 — R7"),
    ([req("R1", kind="hunch")], "R1: `source`"),
    ([req("R1", status="maybe")], "R1: `status`"),
    ([{**req("R1"), "id": "A1"}], "`id` 는 R1, R2"),
    ([req("R1", verify=[{"item": "x"}])], "R1: `verify`"),
])
def test_requirements_are_refused_with_the_item_and_key_named(repo, bad, named):
    [entry] = made(repo, spec_block(requirements=bad))
    assert named in entry["error"]


def test_the_next_task_prompt_documents_the_field():
    text = (Path(__file__).parent / "prompts/next-task.md").read_text(encoding="utf-8")
    assert "`requirements`" in text and all(word in text for word in (*spec_views.KINDS, *spec_views.STATUSES))


def test_original_and_sources_stay_as_stored_through_every_path(repo):
    chat.remember("next", "user", "Keep my drafts, verbatim  please.", repo=repo)
    sid = made(repo, spec_block(slug="drafts", requirements=REQS))[0]["id"]
    spec = specs.load("proj", sid)
    assert spec["original"] == "Keep my drafts, verbatim  please." and spec["spec_version"] == 1

    # The same card emitted again: the requirement's claimed ground changes, the person said more.
    chat.remember("next", "user", "And something else.", repo=repo)
    moved = [{**REQS[0], "source": {"kind": "policy", "locator": "elsewhere"}}, *REQS[1:]]
    assert made(repo, spec_block(slug="drafts", requirements=moved))[0]["id"] == sid
    # The screen's edit, and a plain save with both rewritten.
    assert client().put(f"/api/specs/{sid}", json={"rev": 2, "goal": "New goal", "out": [], "done": []}).status_code == 200
    spec = specs.load("proj", sid)
    specs.save({**spec, "original": "rewritten", "requirements": [{**r, "source": {"kind": "decision", "locator": "x"}}
                                                                   for r in spec["requirements"]]})
    spec = specs.load("proj", sid)
    assert spec["original"] == "Keep my drafts, verbatim  please."
    assert [r["source"] for r in spec["requirements"]] == [r["source"] for r in REQS]

    # A spec-update: a source claim is ignored, a new requirement is a decision, `original` and results are refused.
    results = spec_views.results_file(spec)
    results.write_text('{"R1": {"ok": false}}', encoding="utf-8")
    change = {"rev": spec["rev"], "reason": "R4 found while working", "goal": spec["goal"], "out": [], "done": [],
              "requirements": [*moved, req("R4", kind="original")]}
    specs.revise(repo, spec, change)
    spec = specs.load("proj", sid)
    assert [r["source"] for r in spec["requirements"][:3]] == [r["source"] for r in REQS]
    assert spec["requirements"][3]["source"] == {"kind": "decision", "locator": "spec-update to revision 4: R4 found while working"}
    for key in ("original", "results"):
        with pytest.raises(ValueError, match=key):
            specs.revise(repo, spec, {**change, "rev": spec["rev"], key: "changed"})
    assert specs.load("proj", sid)["original"] == "Keep my drafts, verbatim  please."
    assert results.read_text(encoding="utf-8") == '{"R1": {"ok": false}}' and "results" not in spec


def test_a_requirement_edit_moves_the_version_and_digest_and_a_goal_edit_does_not(repo):
    sid = made(repo, spec_block(slug="versioned", requirements=REQS))[0]["id"]
    first = specs.load("proj", sid)
    assert first["spec_digest"] == spec_views.digest(first["requirements"])

    assert client().put(f"/api/specs/{sid}", json={"rev": 1, "goal": "Only the goal", "out": [], "done": []}).status_code == 200
    goal = specs.load("proj", sid)
    assert (goal["spec_version"], goal["spec_digest"]) == (first["spec_version"], first["spec_digest"]) == (1, first["spec_digest"])

    edited = [{**REQS[0], "text": "R1 keeps the login page under 3 s"}, *REQS[1:]]
    specs.revise(repo, goal, {"rev": goal["rev"], "reason": "slower target", "goal": goal["goal"], "out": [], "done": [],
                              "requirements": edited})
    after = specs.load("proj", sid)
    assert after["spec_version"] == 2 and after["spec_digest"] != first["spec_digest"]
    assert after["revisions"][-1]["requirements"] == first["requirements"]

    # A card emitted again without the section has none, rather than its last one's.
    sid = made(repo, spec_block(slug="dropped", requirements=REQS))[0]["id"]
    assert made(repo, spec_block(slug="dropped"))[0]["id"] == sid
    assert not {"requirements", "spec_version", "spec_digest"} & set(specs.load("proj", sid))


# -- the two views ---------------------------------------------------------------


def test_the_human_document_shows_each_requirement_and_what_is_open():
    text = specs.human(snapshot())
    for part in ("## Purpose", "## Behaviour", "## Constraints", "### R1 — confirmed", "- Source: original — request ¶1",
                 "- Verify: pytest tool/test_a.py ⇒ passes in 30 s", "## Undecided\n\n- R2:",
                 "## Conflicts\n\n- R3 depends on R2, which is undecided", "> Keep my drafts.\n>\n> Please."):
        assert part in text


def test_the_agent_input_holds_the_selection_globals_contracts_and_lists_the_rest():
    spec = snapshot()
    full = specs.agent(spec)
    assert [r["id"] for r in full["requirements"]] == ["R1", "R2", "R3"] and full["omitted"] == []
    assert (full["spec_version"], full["spec_digest"]) == (1, spec["spec_digest"])
    picked = specs.agent(spec, ["R1", "R3"])
    assert [r["id"] for r in picked["requirements"]] == ["R1", "R3"]
    assert picked["contracts"] == [{"id": "R2", "status": "undecided", "text": REQS[1]["text"]}] and picked["omitted"] == []
    assert specs.agent(spec, ["R1"])["omitted"] == ["R2", "R3"]


def test_a_selection_that_would_drop_a_global_or_a_contract_fails_naming_the_ids():
    with pytest.raises(ValueError, match="R1"):
        specs.agent(snapshot(), ["R3"])
    dangling = snapshot()
    dangling["requirements"][2]["depends_on"] = ["R2", "R9"]   # a record written past the checks
    with pytest.raises(ValueError, match="R9"):
        specs.agent(dangling, ["R1", "R3"])
    with pytest.raises(ValueError, match="R8"):
        specs.agent(snapshot(), ["R1", "R8"])


FIXTURES = [snapshot(), snapshot(reqs=[req("R1")], original=""),
            snapshot(reqs=[req("R1", postconditions=["no 2 files"]), req("R2", depends_on=["R1"], **{"global": True})])]


@pytest.mark.parametrize("spec", FIXTURES)
def test_two_renderings_of_one_snapshot_are_equivalent(spec):
    assert specs.equivalent(specs.human(spec), json.dumps(specs.agent(spec))) == \
        {"missing_in_human": [], "missing_in_agent": []}


@pytest.mark.parametrize("old, new, fact", [
    ("### R2 — undecided", "### R2 — confirmed", "status·R2·undecided"),
    ("- Postcondition: the draft is not lost", "- Postcondition: the draft is lost", "negation·R3·not"),
    ("passes in 30 s", "passes in 30 ms", "unit·R1·30s"),
    ("- Verify: open the page ⇒ shows the draft\n", "", "verify·R3·open the page·shows the draft"),
    ("- Depends on: R2\n", "", "depends_on·R3·R2"),
])
def test_a_changed_human_document_is_caught(old, new, fact):
    spec = snapshot()
    text = specs.human(spec)
    assert old in text
    found = specs.equivalent(text.replace(old, new), specs.agent(spec))
    assert fact in found["missing_in_human"]


@pytest.mark.parametrize("change, fact", [
    (lambda a: a["requirements"].pop(1), "id·R2"),
    (lambda a: a["requirements"][0].update(status="assumed"), "status·R1·confirmed"),
    (lambda a: a["requirements"][0]["invariants"].__setitem__(0, "A request leaves the machine"), "negation·R1·no"),
    (lambda a: a["requirements"][2]["failures"].__setitem__(0, "a 500 KB upload"), "unit·R3·500mb"),
    (lambda a: a["requirements"][0].pop("verify"), "verify·R1·pytest tool/test_a.py·passes in 30 s"),
])
def test_a_changed_agent_input_is_caught(change, fact):
    spec = snapshot()
    data = copy.deepcopy(specs.agent(spec))
    change(data)
    assert fact in specs.equivalent(specs.human(spec), data)["missing_in_agent"]


# -- the setting ------------------------------------------------------------------


def test_the_setting_is_off_unless_the_hub_env_says_on(monkeypatch, tmp_path):
    monkeypatch.delenv("SPEC_VIEWS", raising=False)
    assert not spec_views.on()
    for value, expected in (("on", True), ("off", False), ("yes", False)):
        (tmp_path / "hub.env").write_text(f"SPEC_VIEWS={value}\n", encoding="utf-8")
        monkeypatch.setenv("JEV_ENV", str(tmp_path / "hub.env"))
        assert spec_views.on() is expected


def system_for(spec: dict, tmp_path: Path) -> str:
    path = tmp_path / "proj-worktrees" / spec["id"]
    specs.save({**spec, "repo": "proj", "worktree": str(path), "state": "작업 중",
                "history": [{"ts": 1.0}]})
    return specs.system(path)


def test_the_setting_alone_decides_whether_the_agent_view_reaches_the_session(tmp_path, monkeypatch, views):
    spec = snapshot(done=["git --version"], grounds={}, decisions=[])
    prompt = system_for(spec, tmp_path)
    assert spec_views.SECTION in prompt and spec_views.prompted(prompt) == 1 and spec["spec_digest"] in prompt
    monkeypatch.setenv("JEV_ENV", str(tmp_path / "absent.env"))
    saved = specs.load("proj", "fixture")
    assert system_for(spec, tmp_path) == specs.SPEC_PROMPT.rstrip() + "\n\n" + specs.brief(saved)
    bare = {k: v for k, v in saved.items() if k not in ("original", "requirements", "spec_version", "spec_digest")}
    assert specs.brief(saved) == specs.brief(bare)


def test_with_the_setting_off_the_saved_records_brief_byte_identically(monkeypatch):
    """Digests of `brief` over the hub's records as main rendered them; a record revised since is skipped."""
    monkeypatch.delenv("SPEC_VIEWS", raising=False)
    assert not spec_views.on()
    checked = 0
    for sid, want in json.loads(BRIEFS.read_text(encoding="utf-8")).items():
        file = settings.HUB / "raw" / "specs" / "wiki-agent" / f"{sid}.json"
        record = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
        if record is None or record["rev"] != want["rev"]:
            continue
        assert hashlib.sha256(specs.brief(record).encode("utf-8")).hexdigest() == want["sha256"], sid
        checked += 1
    if not checked:
        pytest.skip("no saved record of this hub at its recorded revision")


# -- the first turn ------------------------------------------------------------------


def test_a_stale_prompt_is_rebuilt_before_the_first_turn_and_a_current_one_reused(tmp_path, monkeypatch, views):
    spec = snapshot(done=["git --version"], grounds={}, decisions=[], cell={"model": "m", "effort": "", "fast": False},
                    reqs=[req("R1")])
    path = tmp_path / "proj-worktrees" / "fixture"
    specs.save({**spec, "repo": "proj", "worktree": str(path), "state": "작업 중",
                "history": [{"ts": 1.0}]})
    built = []
    fresh = SimpleNamespace(id="new", _spec_system=specs.system(path))
    monkeypatch.setattr(work, "session", lambda *args: built.append(args) or fresh)

    current = SimpleNamespace(id="old", _spec_system=specs.system(path))
    run = SimpleNamespace(chat=current, session_id="old")
    assert spec_views.first_turn(path, run) == "" and run.chat is current and not built

    stale = SimpleNamespace(id="old", _spec_system=current._spec_system.replace('"spec_version": 1', '"spec_version": 0'))
    run = SimpleNamespace(chat=stale, session_id="old")
    assert spec_views.first_turn(path, run) == ""
    assert run.chat is fresh and run.session_id == "new" and built == [(path, "m", "", False)]


@pytest.mark.parametrize("reqs, held", [
    (REQS, True),                                                  # R3 depends on the undecided R2
    ([req("R1"), req("R2", status="undecided")], False),           # undecided, depended on by nothing
])
def test_a_dependency_on_an_undecided_requirement_holds_the_first_turn(repo, views, reqs, held):
    sid = made(repo, spec_block(slug="held", requirements=reqs))[0]["id"]
    with patch.object(work, "ChatSession", Worker):
        answer = client().post(f"/api/specs/{sid}/start", json={})
        assert answer.status_code == 200, answer.text
        settled(answer.json()["path"])
    [worker] = Worker.made
    rows = work.recall(Path(answer.json()["path"]))
    if held:
        assert worker.heard == [] and "R3 가 정하지 않은 R2 에 기댄다" in rows[-1]["text"]
        assert "R2" in specs.load("proj", sid)["fault"]
    else:
        assert worker.heard[0].startswith("Start.") and spec_views.SECTION in worker.system


# -- verification results and the screen -------------------------------------------------


def test_verification_results_live_beside_the_record_and_an_old_version_is_stale(repo):
    sid = made(repo, spec_block(slug="verified", requirements=REQS))[0]["id"]
    spec = specs.load("proj", sid)
    spec_views.verified(spec, {"ok": True, "head": "abc", "cmd": "git --version"})
    assert spec_views.results(spec) == {"R1": "pass", "R3": "pass"}
    assert "R1" not in json.dumps({k: v for k, v in specs.load("proj", sid).items() if k != "requirements"})
    specs.revise(repo, spec, {"rev": spec["rev"], "reason": "tighter", "goal": spec["goal"], "out": [], "done": [],
                              "requirements": [{**REQS[0], "text": "R1 keeps it under 1 s"}, *REQS[1:]]})
    assert spec_views.results(specs.load("proj", sid)) == {"R1": "stale", "R3": "stale"}
    spec_views.verified(specs.load("proj", sid), {"ok": False, "head": "def", "cmd": "git --version"})
    assert spec_views.results(specs.load("proj", sid)) == {"R1": "fail", "R3": "fail"}


def test_the_views_route_renders_the_saved_record(repo):
    sid = made(repo, spec_block(slug="viewed", requirements=REQS))[0]["id"]
    got = client().get(f"/api/specs/{sid}/views").json()
    spec = specs.load("proj", sid)
    assert set(got) == {"human", "agent", "spec_version", "spec_digest"}
    assert got["human"] == specs.human(spec) and got["agent"] == specs.agent(spec)
    assert (got["spec_version"], got["spec_digest"]) == (1, spec["spec_digest"])
