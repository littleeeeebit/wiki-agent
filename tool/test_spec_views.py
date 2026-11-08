"""A spec's requirements section, its version, and the human and agent views rendered from one snapshot."""

import copy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main import channels
from main import query as chat
from main import spec_views, specs, work
from test_main import client, no_machine_settings, settled  # noqa: F401 — the fixture is autouse
from test_specs import Worker, fresh_workers, made, repo as repository_fixture, spec_block  # noqa: F401 — fixtures

repo = repository_fixture
# Main's off-setting inputs, frozen at fc886a3: the ten saved records' prompt fields with the sha256 of the write
# session's system prompt main built from each, and of the `next` and `refactor` focus introductions.
BASELINE = json.loads((Path(__file__).parent / "fixtures" / "spec_views_baseline.json").read_text(encoding="utf-8"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


class Recorder:
    """A conversation session that keeps the system prompt it was started with."""

    def __init__(self, *args, system="", **kwargs):
        self.system, self.session_id, self.alive, self.is_codex = system, None, True, False


@pytest.mark.parametrize("cid", ["next", "refactor"])
def test_the_planning_foci_learn_the_field_only_with_the_setting_on(repo, monkeypatch, tmp_path, cid):
    monkeypatch.delenv("SPEC_VIEWS", raising=False)
    with patch.object(chat, "ChatSession", Recorder):
        off = chat.session(cid).system
        assert sha(channels.preamble(cid)) == BASELINE["preambles"][cid] and channels.preamble(cid) in off
        assert channels.REQUIREMENTS not in off
        chat._sessions.clear()
        (tmp_path / "hub.env").write_text("SPEC_VIEWS=on\n", encoding="utf-8")
        monkeypatch.setenv("JEV_ENV", str(tmp_path / "hub.env"))
        on = chat.session(cid).system
    assert channels.REQUIREMENTS in on and "`requirements`" in channels.REQUIREMENTS and channels.REQUIREMENTS.isascii()
    assert all(word in channels.REQUIREMENTS for word in (*spec_views.KINDS, *spec_views.STATUSES))


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


def test_the_original_holds_every_word_of_a_conversation_past_the_replay_limit(repo):
    chat.remember("next", "user", "The first request.", repo=repo)
    for n in range(chat.MAX_REPLAY + 10):
        chat.remember("next", "assistant" if n % 2 else "user", f"turn {n}", repo=repo)
    sid = made(repo, spec_block(slug="long", requirements=REQS))[0]["id"]
    original = specs.load("proj", sid)["original"]
    assert original.startswith("The first request.\n\nturn 0\n\n")
    assert original.count("turn ") == (chat.MAX_REPLAY + 10 + 1) // 2


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
                 "- Verify: pytest tool/test_a.py\n  - Expected: passes in 30 s", "## Undecided\n\n- R2:",
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


SAME = {"missing_in_human": [], "missing_in_agent": []}


@pytest.mark.parametrize("spec", FIXTURES)
def test_two_renderings_of_one_snapshot_are_equivalent(spec):
    assert specs.equivalent(specs.human(spec), json.dumps(specs.agent(spec))) == SAME


# Accepted one-line values that look like the document's own structure.
TRICKY = ["- Do not delete drafts", "### R9 — confirmed", "## Constraints", "## Behaviour", "a ⇒ b", "Verify: x", "  - Expected: y",
          "- Expected: z", "Precondition: none", "> quoted", "**bold** `code` [link](x)", "R1 — undecided"]


@pytest.mark.parametrize("value", TRICKY)
def test_any_accepted_value_reads_back_from_the_human_document(value):
    """Property: whatever one-line text a requirement accepts, in any field, the human document carries it
    unambiguously, so two renderings of the snapshot stay equivalent."""
    spec = snapshot(reqs=[req("R1", text=value, preconditions=[value], postconditions=[value], invariants=[value],
                              failures=[value], verify=[{"item": value, "expected": value}],
                              source={"kind": "original", "locator": value}), req("R2", text=value)], goal=value)
    assert specs.equivalent(specs.human(spec), specs.agent(spec)) == SAME


def contracted() -> dict:
    return snapshot(reqs=[req("R1", depends_on=["R2"]), req("R2", invariants=["No draft is lost"],
                                                         postconditions=["saved within 5 s"], preconditions=["a draft"]),
                          req("R3")])


def test_a_selection_compares_through_its_contracts_and_omitted_ids():
    spec = contracted()
    picked = specs.agent(spec, ["R1"])
    assert [c["id"] for c in picked["contracts"]] == ["R2"] and picked["omitted"] == ["R3"]
    assert specs.equivalent(specs.human(spec), picked) == SAME


@pytest.mark.parametrize("change, fact", [
    (lambda a: a["contracts"][0]["invariants"].__setitem__(0, "A draft is lost"), "negation·R2·no"),
    (lambda a: a["contracts"][0].update(status="assumed"), "status·R2·confirmed"),
    (lambda a: a["contracts"][0]["postconditions"].__setitem__(0, "saved within 5 ms"), "unit·R2·5s"),
    (lambda a: a["contracts"].clear(), "id·R2"),
    (lambda a: a["omitted"].clear(), "id·R3"),
    # a contract declared as omitted instead of carried is still owed: the document says R1 depends on R2
    (lambda a: a.update(contracts=[], omitted=["R2", *a["omitted"]]), "condition·R2·invariants·No draft is lost"),
])
def test_a_changed_contract_or_omission_is_caught(change, fact):
    spec = contracted()
    data = copy.deepcopy(specs.agent(spec, ["R1"]))
    change(data)
    assert fact in specs.equivalent(specs.human(spec), data)["missing_in_agent"]


def test_a_global_declared_as_omitted_is_still_owed_in_full():
    spec = FIXTURES[2]   # R2 is global and depends on R1
    data = copy.deepcopy(specs.agent(spec, ["R2"]))
    data.update(requirements=[], contracts=[], omitted=["R1", "R2"])
    found = specs.equivalent(specs.human(spec), data)["missing_in_agent"]
    assert {"status·R2·confirmed", "depends_on·R2·R1", "condition·R1·postconditions·no 2 files"} <= set(found)


# Free text that spans lines and imitates the document's headings, in every free-text place.
LOOSE = ["Sync\n## Behaviour\nNotes", "x\n## Constraints\n### R9 — confirmed\n- Requirement: y", "a\n\n## Undecided\n- R1: z"]


@pytest.mark.parametrize("place", ["goal", "out", "original", "undecided"])
@pytest.mark.parametrize("value", LOOSE)
def test_free_text_anywhere_leaves_the_requirements_readable(place, value):
    """Property: no goal, scope line, original request or undecided text can start a line of the document
    that the requirement parser reads, so every requirement reads back and the two renderings agree."""
    spec = snapshot(**{"goal": {"goal": value}, "out": {"out": [value, "plain"]}, "original": {"original": value},
                       "undecided": {"reqs": [REQS[0], {**REQS[1], "text": value}, REQS[2]]}}[place])
    assert len(spec_views._parsed(specs.human(spec))) == len(spec["requirements"])
    assert specs.equivalent(specs.human(spec), specs.agent(spec)) == SAME


@pytest.mark.parametrize("old, new, fact", [
    ("### R2 — undecided", "### R2 — confirmed", "status·R2·undecided"),
    ("- Postcondition: the draft is not lost", "- Postcondition: the draft is lost", "negation·R3·not"),
    ("passes in 30 s", "passes in 30 ms", "unit·R1·30s"),
    ("- Verify: open the page\n  - Expected: shows the draft\n", "", "verify·R3·open the page·shows the draft"),
    ("  - Expected: shows the draft\n", "", "verify·R3·open the page·shows the draft"),
    ("- Requirement: R3 keeps", "- Requirement: R3 drops", "text·R3·R3 keeps the login page under 2 s and never loses the draft"),
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


def test_with_the_setting_off_the_ten_frozen_records_reach_the_session_byte_identically(monkeypatch, tmp_path):
    """Each frozen record, saved as a started task, gives the write session the same system prompt main gave it.
    All ten are compared: the inputs are committed, nothing is read from this machine's records."""
    monkeypatch.delenv("SPEC_VIEWS", raising=False)
    assert not spec_views.on() and len(BASELINE["records"]) == 10
    for frozen in BASELINE["records"]:
        assert sha(system_for(frozen["record"], tmp_path)) == frozen["system_sha256"], frozen["record"]["id"]


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


CHECKED = [req("R1", verify=[{"item": "python -m ruff check tool", "expected": "clean"}]),
           req("R2", verify=[{"item": "pytest tool/test_a.py", "expected": "passes"},
                             {"item": "open the page", "expected": "shows the draft"}]),
           req("R3", verify=[{"item": "python -m ruff check tool", "expected": "clean"},
                             {"item": "pytest tool/test_a.py", "expected": "passes"}])]


def gated(*commands, ok=True, head="abc") -> dict:
    return {"ok": ok, "head": head, "cmd": " && ".join(commands), "commands": list(commands)}


def test_only_a_verify_item_the_gate_ran_is_recorded(repo):
    """Property: a requirement passes only when every one of its verify items was itself a command the
    server's gate ran and passed under this version; another check passing proves nothing."""
    sid = made(repo, spec_block(slug="verified", requirements=CHECKED))[0]["id"]
    spec = specs.load("proj", sid)
    spec_views.verified(spec, gated("git --version"))
    assert spec_views.results(spec) == {"R1": "missing", "R2": "missing", "R3": "missing"}
    assert not spec_views.results_file(spec).exists()
    spec_views.verified(spec, gated("python -m ruff check tool"))
    assert spec_views.results(spec) == {"R1": "pass", "R2": "missing", "R3": "missing"}
    spec_views.verified(spec, gated("pytest tool/test_a.py"))
    assert spec_views.results(spec) == {"R1": "pass", "R2": "missing", "R3": "pass"}
    spec_views.verified(spec, gated("pytest  tool/test_a.py", ok=False))
    assert spec_views.results(spec) == {"R1": "pass", "R2": "fail", "R3": "fail"}
    assert "items" not in json.dumps({k: v for k, v in specs.load("proj", sid).items() if k != "requirements"})


def test_passes_at_different_commits_never_add_up_to_one(repo):
    """Property: a requirement passes only when all its verify items passed at one commit."""
    sid = made(repo, spec_block(slug="heads", requirements=CHECKED))[0]["id"]
    spec = specs.load("proj", sid)
    spec_views.verified(spec, gated("python -m ruff check tool", head="a1"))
    spec_views.verified(spec, gated("pytest tool/test_a.py", head="b2"))
    assert spec_views.results(spec)["R3"] == "missing"
    spec_views.verified(spec, gated("python -m ruff check tool", head="b2"))
    assert spec_views.results(spec)["R3"] == "pass"


def test_a_result_recorded_under_an_earlier_version_is_stale(repo):
    sid = made(repo, spec_block(slug="stale", requirements=CHECKED))[0]["id"]
    spec = specs.load("proj", sid)
    spec_views.verified(spec, gated("python -m ruff check tool"))
    specs.revise(repo, spec, {"rev": spec["rev"], "reason": "tighter", "goal": spec["goal"], "out": [], "done": [],
                              "requirements": [{**CHECKED[0], "text": "R1 is tighter"}, *CHECKED[1:]]})
    assert spec_views.results(specs.load("proj", sid)) == {"R1": "stale", "R2": "missing", "R3": "stale"}
    spec_views.verified(specs.load("proj", sid), gated("python -m ruff check tool"))
    assert spec_views.results(specs.load("proj", sid))["R1"] == "pass"


def test_the_views_route_renders_the_saved_record(repo):
    sid = made(repo, spec_block(slug="viewed", requirements=REQS))[0]["id"]
    got = client().get(f"/api/specs/{sid}/views").json()
    spec = specs.load("proj", sid)
    assert set(got) == {"human", "agent", "spec_version", "spec_digest"}
    assert got["human"] == specs.human(spec) and got["agent"] == specs.agent(spec)
    assert (got["spec_version"], got["spec_digest"]) == (1, spec["spec_digest"])


# -- the experiment harness (`tool/eval/spec_views.py`), driven by a scripted stub ---------------

from eval import spec_views as harness  # noqa: E402
from eval import spec_views_report as spec_report  # noqa: E402


def _git(cwd, *args):
    return harness.git(Path(cwd), *args)


@pytest.fixture
def replay(tmp_path):
    """A source repository whose one 'pull request' fixes `add`, its frozen spec and an experiment config."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (src / "test_other.py").write_text("def test_other():\n    assert True\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    base = _git(src, "rev-parse", "HEAD").strip()
    (src / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "test_pkg.py").write_text("from pkg import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                     encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "the answer (#1)")
    merge = _git(src, "rev-parse", "HEAD").strip()
    hidden = (src / "test_pkg.py").read_bytes()
    enriched = snapshot(goal="add adds", out=["Nothing else"])
    record = {"task": "t1", "baseline": {"id": "fixture", "rev": 1, "goal": "add adds", "out": ["Nothing else"],
                                        "done": ["python -m pytest"], "decisions": []},
              "enriched": enriched, "select": ["R1"], "selection_cost": {"usd": 0.01}}
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "t1.json").write_text(json.dumps(record), encoding="utf-8")
    task = {"id": "t1", "merge_base": base, "merge_commit": merge, "spec_digest": enriched["spec_digest"],
            "hidden": [{"path": "test_pkg.py", "tests": ["test_add"], "digest": harness.sha(hidden)}],
            "affected": [{"path": "test_other.py"}]}
    cfg = {"schema": harness.SCHEMA, "experiment": "t", "kind": "pilot", "config_digest": "d", "seed": 7,
           "journal_dir": str(tmp_path / "raw"), "hidden_dir": str(tmp_path / "hidden"), "source_repo": str(src),
           "tasks": [task], "repetitions": 1, "concurrency": 1, "implementer": {"name": "claude-cli", "model": "m"},
           "ceilings": {"usd": 10, "hours": 1},
           "limits": {"cell_usd": 1, "min_cell_usd": 0.05, "session_seconds": 60, "test_seconds": 120,
                      "gate_seconds": 60},
           "report": {"resamples": 200, "markdown": "x.md"}, "runner_difference": "stub", "path": "c.json"}
    store = harness.stored(cfg, "t1", "test_pkg.py")
    store.parent.mkdir(parents=True)
    store.write_bytes(hidden)
    return SimpleNamespace(cfg=cfg, folder=folder, src=src, merge=merge, base=base, scratch=tmp_path / "scratch")


def stub(script):
    """A scripted implementer: `script(workspace, system, stop)` edits the workspace and may name a stop cause."""
    def impl(ws, system, limits, stop):
        cause = script(Path(ws), system, stop) or ""
        return {"result": "```done-report\n[{\"item\": \"x\", \"pass\": true}]\n```", "usd": None if cause else 0.1,
                "tokens": {"input": 1}, "turns": 1, "error": "", "cause": cause, "infra": False}
    impl.name = "stub"
    return impl


def fix(ws, system, stop):
    (ws / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


def ran(cfg, kinds=("cell",)):
    return [r for r in harness.rows(cfg) if r["kind"] in kinds]


def test_every_cell_starts_alone_at_the_merge_base_and_leaves_nothing_behind(replay):
    seen = []

    def script(ws, system, stop):
        seen.append({"ws": ws, "log": _git(ws, "log", "--all", "--format=%H").split(),
                     "answer": harness.git(ws, "cat-file", "-t", replay.merge, check=False).strip(),
                     "left": (ws / "left.txt").exists(), "hidden": (ws / "test_pkg.py").exists(),
                     "d_file": (ws / harness.D_FILE).exists(), "system": system})
        (ws / "left.txt").write_text("from an earlier cell", encoding="utf-8")
        fix(ws, system, stop)

    got = harness.run(replay.cfg, stub(script), stub(script), replay.folder, replay.scratch)
    assert got == {"stopped": None, "pending": 0, "spent": pytest.approx(0.5)}
    rows = ran(replay.cfg, ("cell", "production_baseline"))
    assert len(seen) == len(rows) == 5   # four conditions, then the production baseline
    # Concurrency 1: the cells ran in exactly the seeded shuffled order.
    assert [(r["task"], r["condition"], r["repetition"]) for r in rows[:4]] == harness.cells(replay.cfg)
    for s in seen:
        assert len(s["log"]) == 1 and not s["answer"]   # one commit; the merge commit is not reachable
        assert not s["left"] and not s["hidden"]        # nothing from an earlier cell, no hidden test yet
        assert not s["ws"].exists()                     # removed after evaluation
    systems = {r["condition"] + r["kind"][0]: s["system"] for r, s in zip(rows, seen)}
    assert "### R1" in systems["Bc"] and '"requirements"' in systems["Cc"] and harness.D_NOTE in systems["Dc"]
    assert [s["d_file"] for s in seen] == [r["condition"] == "D" for r in rows]
    cells = ran(replay.cfg)
    assert all(r["success"] and r["hidden"] == {"test_pkg.py::test_add": "passed"} for r in cells)
    assert all(r["spec_digest"] == replay.cfg["tasks"][0]["spec_digest"] and r["usd"] == 0.1 for r in cells)
    assert next(r for r in cells if r["condition"] == "D")["input_construction"] == {"usd": 0.01}


def one_cell(replay, script, condition="A"):
    record = harness.frozen("t1", replay.folder)
    ledger = harness.Ledger(replay.cfg, [])
    return harness.run_cell(replay.cfg, replay.cfg["tasks"][0], record, condition, 1, stub(script), ledger,
                            ledger.reserve(1, 0), threading.Event(), scratch=replay.scratch)


def test_a_session_that_does_not_implement_fails_whatever_its_done_report_says(replay):
    row = one_cell(replay, lambda ws, system, stop: None)
    assert not row["success"] and row["failure"] == "verification"
    assert row["hidden"] == {"test_pkg.py::test_add": "failed"}
    assert row["done_report"] == [{"item": "x", "pass": True}]   # kept, never the evidence


def test_cells_are_shuffled_by_the_seed_and_a_finished_cell_is_not_run_again(replay):
    cfg = {**replay.cfg, "tasks": [replay.cfg["tasks"][0], {**replay.cfg["tasks"][0], "id": "t2"}],
           "repetitions": 2}
    order = harness.cells(cfg)
    assert order == harness.cells(cfg)   # the seed fixes it
    assert sorted(order) == sorted((t, c, r) for t in ("t1", "t2") for c in "ABCD" for r in (1, 2))
    assert order != sorted(order) and order != harness.cells({**cfg, "seed": 8})
    infra = {"kind": "cell", "task": "t1", "condition": "A", "repetition": 1, "infra": True}
    final = {"kind": "cell", "task": "t1", "condition": "B", "repetition": 1, "success": False}
    left = harness.pending(cfg, [infra, final])
    assert ("t1", "A", 1) in left and ("t1", "B", 1) not in left and len(left) == 15   # an infra error runs once more
    assert ("t1", "A", 1) not in harness.pending(cfg, [infra, {**infra, "infra": True}])   # twice is final


def test_a_config_that_is_not_the_committed_one_is_refused(replay, tmp_path):
    repo = tmp_path / "hub"
    repo.mkdir()
    _git(repo, "init", "-q")
    file = repo / "pilot.json"
    file.write_text(json.dumps({"schema": harness.SCHEMA, "experiment": "p"}), encoding="utf-8")
    with pytest.raises(harness.Refused, match="not committed"):
        harness.config(file, repo)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "config")
    assert harness.config(file, repo)["config_digest"] == harness.sha(file.read_bytes())
    file.write_text(json.dumps({"schema": harness.SCHEMA, "experiment": "p", "repetitions": 3}), encoding="utf-8")
    with pytest.raises(harness.Refused, match="new experiment id"):
        harness.config(file, repo)
    with pytest.raises(harness.Refused, match="frozen spec digest"):
        harness.run({**replay.cfg, "tasks": [{**replay.cfg["tasks"][0], "spec_digest": "0" * 64}]},
                    stub(fix), stub(fix), replay.folder, replay.scratch)
    harness.stored(replay.cfg, "t1", "test_pkg.py").write_text("def test_add():\n    pass\n", encoding="utf-8")
    with pytest.raises(harness.Refused, match="hidden test test_pkg.py digest"):
        harness.run(replay.cfg, stub(fix), stub(fix), replay.folder, replay.scratch)
    assert not ran(replay.cfg)   # a refused check records no result


@pytest.mark.parametrize("path", [harness.D_FILE, "eval/spec-views/pilot-1.json", "raw/specs/x/t1.json",
                                  "test_hidden_pkg.py"])
def test_a_forbidden_change_fails_the_cell_even_when_every_test_passes(replay, path):
    def script(ws, system, stop):
        fix(ws, system, stop)
        (ws / path).parent.mkdir(parents=True, exist_ok=True)
        (ws / path).write_text("changed by the session", encoding="utf-8")

    row = one_cell(replay, script, "D")
    assert row["hidden"]["test_pkg.py::test_add"] == "passed"
    assert not row["success"] and row["forbidden"] == [path] and row["failure"] == "verification"


def test_an_edited_affected_test_is_judged_as_the_merge_base_wrote_it(replay):
    def script(ws, system, stop):
        fix(ws, system, stop)
        (ws / "test_other.py").write_text("def test_other():\n    assert False\n", encoding="utf-8")

    row = one_cell(replay, script)
    assert row["success"] and row["affected_edited"] == ["test_other.py"]
    assert row["affected"]["test_other.py"]["ok"]


def test_a_ceiling_stop_is_journalled_as_a_failure_and_the_rest_is_left_to_resume(replay):
    def script(ws, system, stop):
        fix(ws, system, stop)
        assert stop.wait(30)   # a long session the wall-time ceiling ends
        return "ceiling"

    cfg = {**replay.cfg, "ceilings": {"usd": 10, "hours": 2 / 3600}}
    got = harness.run(cfg, stub(script), stub(fix), replay.folder, replay.scratch)
    cells = ran(cfg)
    assert len(cells) == 1 and not cells[0]["success"] and cells[0]["failure"] == "ceiling"
    assert cells[0]["usd"] is None and cells[0]["usd_bound"] == 1   # an unknown cost is charged at its cap
    stop = ran(cfg, ("ceiling_stop",))[0]
    assert stop["ceiling"] == "wall" and stop["spent"] == 1 and stop["pending"] == 3 == got["pending"]
    rep = spec_report.report(cfg)
    assert len(rep["cells_not_run"]) == 3
    assert rep["production_baseline"]["t1"] == {"not_run": "the wall ceiling stopped the run first"}
    assert rep["mock_rows_excluded"] == 1 and rep["per_condition"][cells[0]["condition"]]["cells_run"] == 0
    assert "pilot, not the main experiment" in spec_report.markdown(rep, cfg)


def test_the_usd_ceiling_stops_launching_before_a_cell_could_cross_it(replay):
    cfg = {**replay.cfg, "ceilings": {"usd": 0.15, "hours": 1}}
    harness.run(cfg, stub(fix), stub(fix), replay.folder, replay.scratch)
    assert [r["usd_cap"] for r in ran(cfg)] == [0.15]   # the cap is the room left
    assert ran(cfg, ("ceiling_stop",))[0]["ceiling"] == "usd"
