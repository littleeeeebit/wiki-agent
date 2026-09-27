"""Stage 9 of `docs/plans/jev/`: the app's Jev settings, a question's run as
numbered events and a durable trace, reattachment, cancellation, the evidence
and graph paths a summary shows, and one run shape for the app and the CLI.

No credentials and no external calls: Jev, the host session and retrieval
are fakes. Every trace lands under the test's own `JEV_ENV` folder.
"""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import decision
import jev_search
from agent.chat_session import Event
from common.budget import Budget
from main import app as main_app
from main import channels as chat_channels
from main import knowledge, specs
from main import query as chat
from test_decision_flow import MODEL, POLICY, REPO, World, answering, chunk, found
from test_grounded_answer import REJECTED, Judge, Screen, events_of, mixed, session_saying

KEY = "obs-secret-key-4711"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A `.env` of the test's own; traces and app settings beside it."""

    monkeypatch.setattr(decision.claims, "ARTIFACT", Path("absent") / "relation-policy.json")
    env = tmp_path / "jev.env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))
    monkeypatch.setattr(knowledge, "LIVE", {})
    yield env


@pytest.fixture
def active(tmp_path, isolated, monkeypatch):
    isolated.write_text(f"TYPESAFE_API_KEY={KEY}\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    judge = Judge()
    monkeypatch.setattr(knowledge.DECISIONS, "_items", type(knowledge.DECISIONS._items)())  # no earlier test's verdicts
    monkeypatch.setattr(decision, "evaluate", lambda cfg, state, questions, trace, budget=None, stage="":
                        judge(state, questions, trace, budget, stage))
    with patch.object(chat_channels, "LOCAL", {}), patch.object(chat, "LOGS", tmp_path / "chat"), \
         patch.object(chat, "_project", None), patch.object(chat, "_config", {}), \
         patch.object(chat, "_sessions", {}), patch.object(chat, "_busy", {}), \
         patch.object(chat, "hits_for", return_value=[]), patch.object(specs, "SPECS", tmp_path / "specs"), \
         patch.object(chat, "explain", lambda *a: iter([Event("done", "쉬운 설명")])):
        yield judge


def web() -> Screen:
    return Screen(main_app.app, base_url="http://127.0.0.1:8787")


def retrieved(d: dict):
    """`prepare` as the run sees it: the dossier, and the step a real one emits."""

    def prepare(question, project, state="", k=8, cfg=None, run=None, **_):
        if run is not None:
            run.dossier = d
            run.step("retrieved", d["status"], evidence=[e["chunk_id"] for e in d["evidence"]])
        return d

    return prepare


# -- settings ------------------------------------------------------------------------------

def test_the_app_settings_override_the_file_and_a_run_keeps_its_snapshot(tmp_path, isolated):
    isolated.write_text(f"TYPESAFE_API_KEY={KEY}\nWIKI_JEV_MODE=shadow\n", encoding="utf-8")
    assert (decision.config().mode, decision.config().mode_source) == ("shadow", "file")
    cfg = decision.save({"mode": "active", "disabled_sources": ["memory"], "limits": {"calls": 3, "seconds": 30}})
    assert (cfg.mode, cfg.mode_source, cfg.disabled) == ("active", "app", ("memory",))
    assert cfg.limits == {"seconds": 30.0, "calls": 3, "candidates": 40}
    assert KEY not in decision.settings_file().read_text(encoding="utf-8") + json.dumps(cfg.status())
    run = knowledge.Run(tmp_path, "wiki", "q", cfg)
    decision.save({"mode": "off"})
    assert decision.config().mode == "off" and run.cfg.mode == "active", "a run keeps the settings it started with"
    decision.save({"mode": None})
    assert (decision.config().mode, decision.config().mode_source) == ("shadow", "file")


@pytest.mark.parametrize("bad", [{"mode": "loud"}, {"limits": {"calls": 0}}, {"limits": {"calls": 2.5}},
                                 {"limits": {"tokens": 5}}, {"disabled_sources": ["web"]}, {"key": "x"}])
def test_bad_settings_are_refused(bad):
    with pytest.raises(ValueError):
        decision.save(bad)
    assert not decision.settings_file().exists()


def test_unreadable_settings_turn_jev_off_and_say_so(isolated):
    isolated.write_text(f"TYPESAFE_API_KEY={KEY}\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    decision.settings_file().parent.mkdir(parents=True)
    decision.settings_file().write_text("{not json", encoding="utf-8")
    cfg = decision.config()
    assert (cfg.mode, cfg.problem, cfg.status()["health"]) == ("off", "unreadable_settings", "unavailable")


def test_switched_off_sources_are_not_searched_and_the_limits_bound_the_run(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert knowledge.available(repo, ("memory",)) == ["hub", "documents"]
    assert knowledge.available(repo, decision.FAMILIES) == ["hub"], "a question always searches somewhere"
    asked = []
    monkeypatch.setattr(knowledge, "run_round", lambda req, project, budget: asked.append(req) or None)
    cfg = decision.Config("off", MODEL, "default", disabled=("hub",), limits={"seconds": 9.0, "calls": 2,
                                                                              "candidates": 7})
    out = knowledge.prepare("Where is the port?", repo, cfg=cfg)
    assert out["budget"]["limits"] == {"seconds": 9.0, "calls": 2, "candidates": 7, "tokens": None}
    assert asked[0]["source_allowlist"] == ["documents", "memory"]


def test_the_settings_route_saves_and_never_shows_the_key(active):
    screen = web()
    saved = screen.post("/api/jev/settings", json={"mode": "shadow", "disabled_sources": ["papers"],
                                                   "limits": {"calls": 4}})
    assert saved.status_code == 200 and KEY not in saved.text
    assert saved.json()["mode"] == "shadow" and saved.json()["mode_source"] == "app"
    assert saved.json()["disabled_sources"] == ["papers"] and saved.json()["limits"]["calls"] == 4
    refused = screen.post("/api/jev/settings", json={"mode": "loud"})
    assert refused.status_code == 400 and screen.get("/api/jev").json()["mode"] == "shadow"
    status = screen.get("/api/knowledge/status").json()
    assert KEY not in json.dumps(status) and "papers" not in status["sources"]["searched"]


# -- events and the trace ---------------------------------------------------------------

def test_every_event_is_numbered_and_traced_without_its_pieces_or_the_key(tmp_path):
    cfg = decision.Config("active", MODEL, "file", key=KEY)
    run = knowledge.Run(tmp_path, "wiki", "q", cfg)
    run.follow(Budget(seconds=30, calls=6, candidates=40))
    run.put({"kind": "delta", "text": "a piece"})
    run.step("retrieve", "retrieval_needed", sources=["documents"])
    run.put({"kind": "error", "code": "host_failed", "text": f"401 for key {KEY}"})
    assert [e["seq"] for e in run.events] == [0, 1, 2, 3] and run.events[0]["status"] == "started"
    for e in run.events:
        assert {"run_id", "seq", "stage", "status", "elapsed_ms", "budget_remaining"} <= set(e)
    assert run.events[2]["budget_remaining"]["calls"] == 6 and run.events[3]["stage"] == "retrieve"
    assert KEY not in json.dumps(run.events), "the events a screen tails carry no key either"
    summary = run.finish("failed", "host_failed")
    trace = (run.folder / f"{run.id}.jsonl").read_text(encoding="utf-8")
    assert "a piece" not in trace, "a streamed piece is not traced"
    assert KEY not in trace and "[redacted]" in trace
    assert all(json.loads(line)["repo_id"] == run.repo_id for line in trace.splitlines())
    assert json.loads((run.folder / f"{run.id}.json").read_text(encoding="utf-8")) == summary
    assert summary["outcome"] == "failed" and {"code": "failed", "reason": "host_failed"} in summary["notes"]


def test_a_cap_reached_is_not_called_a_source_down(tmp_path):
    run = knowledge.Run(tmp_path, "wiki", "q", decision.Config("active", MODEL, "file", key=KEY))
    run.dossier = {"status": "ready", "evidence": [],
                   "limits": [{"round": 1, "retrieval": ["candidates"]}, {"baseline": "retrieval_unavailable"}]}
    notes = run.finish("answered")["notes"]
    assert {"code": "truncated", "limits": ["candidates"]} in notes
    assert {"code": "source_unavailable", "detail": "retrieval_unavailable"} in notes


def test_a_run_records_its_graph_walk_and_the_summary_draws_the_same_paths(tmp_path):
    seed, bridge = chunk("ports", "The daemon listens on port 8791."), chunk("owner", "Atlas owns the daemon.",
                                                                            lane="graph")
    walked = {"lane": "graph", "seed": seed["chunk_id"], "to": bridge["chunk_id"], "hops": 1, "status": "discovered",
              "steps": [{"node": seed["chunk_id"], "node_kind": "chunk"},
                        {"node": bridge["chunk_id"], "node_kind": "chunk", "edge_id": "edge-1", "kind": "links_to",
                         "reverse": False, "origin": "deterministic", "confidence": 1.0}]}
    world = World(answering(), [found([seed, bridge], paths=[walked])])
    run = knowledge.Run(tmp_path, "wiki", "q", decision.Config("active", MODEL, "file", key=KEY))
    budget = Budget(seconds=30, calls=6, candidates=40)
    run.follow(budget)
    flow = knowledge.Flow("Who owns the daemon on port 8791?", "", 8, omitted=None, available=["documents"],
                          repo_id=REPO, graph=True, model=MODEL, live=True, budget=budget, pol=POLICY,
                          evaluate=world.evaluate, normalize=lambda texts, s, o=None: [
                              {"text": t, "status": "original_english", "language": "en", "version": "en/1"}
                              for t in texts], first=world.first, mend=world.mend, emit=run.step)
    run.dossier = flow.run()
    expand = next(e for e in run.events if e["stage"] == "expand")
    assert expand["walked"] == [walked]
    assert {c["chunk_id"]: c["lane"] for c in expand["candidates"]} == {seed["chunk_id"]: "rrf",
                                                                       bridge["chunk_id"]: "graph"}
    assert [e["stage"] for e in run.events][:5] == ["start", "route", "retrieve", "expand", "grade"]
    graph = run.finish("answered")["graph"]
    assert graph["paths"] == [walked], "the map draws the traversal the run recorded"
    assert graph["seeds"] == [seed["chunk_id"]] and graph["bridges"] == [bridge["chunk_id"]]
    assert graph["edges"]["edge-1"] == {"kind": "links_to", "directed": True, "origin": "deterministic",
                                        "confidence": 1.0, "spans": []}


# -- the app's question, as a run ---------------------------------------------------------

def test_a_question_is_a_run_whose_steps_evidence_and_summary_the_screen_reads(tmp_path, active):
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    screen = web()
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session):
        events = events_of(screen.post("/api/say/wiki", json={"text": "데몬 포트는?"}))
    run_id = events[0]["run_id"]
    assert {e["run_id"] for e in events} == {run_id} and [e["seq"] for e in events] == list(range(len(events)))
    steps = [(e["stage"], e["status"]) for e in events if e["kind"] == "step"]
    assert steps[:5] == [("start", "started"), ("retrieved", "ready"), ("draft", "writing"), ("verify", "checked"),
                         ("publish", "complete")]
    verify = next(e for e in events if e["kind"] == "step" and e["stage"] == "verify")
    assert {c["claim_id"]: c["state"] for c in verify["claims"]} == {"c1": "accepted", "c2": "rejected"}
    assert REJECTED not in json.dumps(events, ensure_ascii=False), "a check shows states, never a claim's text"
    rows = chat.recall("wiki", include_context=True)
    assert {r["run_id"] for r in rows if r["role"] in ("user", "assistant")} == {run_id}

    summary = screen.get(f"/api/knowledge/runs/{run_id}").json()
    assert summary["outcome"] == "complete" and summary["settings"]["mode"] == "active"
    support = {e["cite"]: e["support"] for e in summary["evidence"]}
    assert support == {"docs/ports.md:3": "supported", "docs/owners.md:3": "not_cited"}
    ports = summary["evidence"][0]
    assert ports["revision"] == d["evidence"][0]["revision"] and ports["text_en"] and ports["kind"] == "document"
    assert {c["claim_id"]: c["reason"] for c in summary["claims"]} == {"c1": None, "c2": "fabricated_quote"}
    assert KEY not in json.dumps(summary) and KEY not in (knowledge.runs_root() / summary["repo_id"] / "runs"
                                                          / f"{run_id}.jsonl").read_text(encoding="utf-8")

    done = next(e["seq"] for e in events if e["kind"] == "done")
    later = events_of(screen.get(f"/api/knowledge/runs/{run_id}/events", params={"after": done}))
    assert [e["seq"] for e in later] == [e["seq"] for e in events if e["seq"] > done]

    knowledge.LIVE.clear()   # the server restarted: the trace answers
    assert screen.get(f"/api/knowledge/runs/{run_id}").json() == summary
    again = events_of(screen.get(f"/api/knowledge/runs/{run_id}/events", params={"after": -1}))
    assert [e["seq"] for e in again] == [e["seq"] for e in events if e["kind"] not in knowledge.UNTRACED]


def test_a_cited_file_changed_or_removed_since_the_run_says_only_its_snapshot_remains(tmp_path, active):
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    screen = web()
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session):
        run_id = events_of(screen.post("/api/say/wiki", json={"text": "데몬 포트는?"}))[0]["run_id"]

    def now():
        return {e["cite"]: e["now"] for e in screen.get(f"/api/knowledge/runs/{run_id}").json()["evidence"]}

    assert now() == {"docs/ports.md:3": "same", "docs/owners.md:3": "same"}
    (tmp_path / "docs" / "ports.md").write_text("# ports\n\nThe daemon moved to 9000.\n", encoding="utf-8")
    (tmp_path / "docs" / "owners.md").unlink()
    knowledge.LIVE.clear()
    assert now() == {"docs/ports.md:3": "changed", "docs/owners.md:3": "missing"}
    assert "now" not in knowledge.stored(run_id, chat.current_repo())[0]["evidence"][0], "the record is not rewritten"


def test_another_projects_run_is_not_found_by_its_id(tmp_path, active):
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    screen = web()
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session):
        run_id = events_of(screen.post("/api/say/wiki", json={"text": "데몬 포트는?"}))[0]["run_id"]
    other = tmp_path / "other"
    other.mkdir()
    with patch.object(chat, "current_repo", return_value=other):
        for url in (f"/api/knowledge/runs/{run_id}", f"/api/knowledge/runs/{run_id}/events"):
            assert screen.get(url).status_code == 404
        assert screen.post(f"/api/knowledge/runs/{run_id}/cancel").status_code == 404
    assert knowledge.export(run_id, other) is None
    assert screen.get("/api/knowledge/runs/../../etc").status_code == 404


def test_a_stop_ends_the_run_cancelled_and_publishes_nothing(tmp_path, active):
    d, _reply = mixed(tmp_path)
    heard = threading.Event()

    class Waiting:
        def say(self, text, halt=None):
            heard.set()
            halt.wait(10)
            yield Event("error", "멈췄다.")

    screen = web()
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=Waiting()), \
         patch.object(chat, "streaming", lambda events: events):
        stream = chat.say("wiki", chat.Say(text="데몬 포트는?"))
        first = json.loads(next(stream)[6:])
        assert heard.wait(5)
        assert screen.post(f"/api/knowledge/runs/{first['run_id']}/cancel").json()["ok"]
        rest = [json.loads(chunk[6:]) for chunk in stream if chunk.startswith("data: ")]
    kinds = [e["kind"] for e in rest]
    assert "cancelled" in kinds and "done" not in kinds and "error" not in kinds
    run = knowledge.LIVE[first["run_id"]]
    assert run.summary["outcome"] == "cancelled" and run.summary["verification"] is None
    row = chat.recall("wiki")[-1]
    assert row["cancelled"] and row["error"] == "멈췄다" and not row.get("verification")
    assert "wiki" not in chat._busy


@pytest.mark.parametrize("mode", ["active", "off"])
@pytest.mark.parametrize("stopped_first", [True, False])
def test_a_stop_and_the_publication_never_interleave(tmp_path, isolated, active, mode, stopped_first):
    isolated.write_text(f"TYPESAFE_API_KEY={KEY}\nWIKI_JEV_MODE={mode}\n", encoding="utf-8")
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    seal, said = knowledge.Run.seal, []

    def racing(run):   # the stop lands right before, or right after, the point of no return
        if stopped_first:
            said.append(chat.knowledge_cancel(run.id))
        sealed = seal(run)
        if not stopped_first:
            said.append(chat.knowledge_cancel(run.id))
        return sealed

    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session), \
         patch.object(knowledge.Run, "seal", racing):
        events = events_of(web().post("/api/say/wiki", json={"text": "데몬 포트는?"}))
    run = knowledge.LIVE[events[0]["run_id"]]
    kinds = [e["kind"] for e in events]
    assert said == [{"ok": True, "done": False, "published": not stopped_first}]
    if stopped_first:
        assert "cancelled" in kinds and "done" not in kinds and run.summary["outcome"] == "cancelled"
        assert chat.recall("wiki")[-1]["cancelled"]
    else:
        assert "done" in kinds and "cancelled" not in kinds, "a stop after publication does not take it back"
        v = run.summary["verification"]
        assert run.summary["outcome"] == (v["status"] if mode == "active" else "answered")
        assert "simple_start" not in kinds, "it only cuts the plain explanation"


def test_a_run_the_server_went_down_with_ends_interrupted(tmp_path, active):
    run = knowledge.Run(chat.current_repo(), "wiki", "q", decision.config())   # down before its first step
    knowledge.LIVE.clear()
    screen = web()
    assert screen.get(f"/api/knowledge/runs/{run.id}").json()["outcome"] == "interrupted"
    events = events_of(screen.get(f"/api/knowledge/runs/{run.id}/events"))
    assert [e["kind"] for e in events] == ["step", "error"] and events[-1]["code"] == "interrupted"


def test_status_lists_this_projects_running_runs_for_a_screen_that_reloaded(tmp_path, active):
    here = knowledge.Run(chat.current_repo(), "wiki", "q", decision.config())
    elsewhere = knowledge.Run(tmp_path, "wiki", "q", decision.config())
    runs = web().get("/api/knowledge/status").json()["runs"]
    assert [r["run_id"] for r in runs] == [here.id]
    here.finish("answered")
    elsewhere.finish("answered")
    assert web().get("/api/knowledge/status").json()["runs"] == []


def test_an_export_leaves_out_every_text_unless_asked(tmp_path, active):
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session):
        run_id = events_of(web().post("/api/say/wiki", json={"text": "데몬 포트는 비밀?"}))[0]["run_id"]
    repo = chat.current_repo()
    bare = json.dumps(knowledge.export(run_id, repo), ensure_ascii=False)
    assert "8791" not in bare and "비밀" not in bare and "listens" not in bare
    assert d["evidence"][0]["revision"] in bare and "docs/ports.md" in bare, "hashes and locators stay"
    assert "8791" in json.dumps(knowledge.export(run_id, repo, text=True), ensure_ascii=False)


# -- the app and the command line -------------------------------------------------------------

def test_the_app_and_the_cli_publish_the_same_decisions_and_evidence_for_one_input(tmp_path, active):
    d, reply = mixed(tmp_path)
    session, _sent = session_saying(reply)
    with patch.object(chat, "prepare", retrieved(d)), patch.object(chat, "session", return_value=session):
        run_id = events_of(web().post("/api/say/wiki", json={"text": "데몬 포트는?"}))[0]["run_id"]
    app = knowledge.LIVE[run_id].summary

    host, _heard = session_saying(reply)

    class Host:
        def __init__(self, *args, **kwargs):
            pass

        def say(self, text, halt=None):
            return host.say(text)

        def close(self):
            pass

    with patch.object(jev_search, "prepare", retrieved(d)), patch("agent.ChatSession", Host):
        cli = jev_search.answer("데몬 포트는?", str(tmp_path), "", 8, "")

    def identity(s):
        v = s["verification"]
        return {"outcome": s["outcome"], "evidence": [(e["chunk_id"], e["revision"], e["support"]) for e in s["evidence"]],
                "claims": [(c["claim_id"], c["state"], c["reason"]) for c in s["claims"]],
                "published": [(c["claim_id"], tuple(c["evidence_ids"]), c["support"]) for c in v["claims"]],
                "citations": [c["evidence_id"] for c in v["citations"]]}

    assert identity(cli) == identity(app)
    assert cli["answered"] and cli["answered"] == app["answered"], "both publish the same text"
    assert cli["schema_version"] == app["schema_version"] == knowledge.RUN_SUMMARY
    assert knowledge.stored(cli["run_id"], tmp_path)[0] == cli, "the CLI's run is traced where the app's are"
