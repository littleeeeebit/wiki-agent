"""`generate.rebuild`: a kind's candidates cut again from a split's claimed runs, with no teacher."""

import json

import pytest

import gate
import generate
from store import read

NOUL = {"type": "noul", "instructions": "Is the passage useful?"}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def claim(query, passages=3):
    state = {"query": query, "passages": [{"id": f"p{k}", "text": " ".join(f"{query}{k}w{j}" for j in range(80))}
                                          for k in range(passages)]}
    return {"state": state, "questions": {f"useful_p{k}": {"kind": "useful", "question": NOUL}
                                          for k in range(passages)}}


def test_rebuild_cuts_a_kinds_candidates_from_the_claims_of_one_split(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "DATA", tmp_path / "data")
    monkeypatch.setattr(gate, "POOL", tmp_path / "pool.jsonl")
    monkeypatch.setattr(gate, "TESTS", tmp_path / "tests.json")
    write(tmp_path / "pool.jsonl", [])
    (tmp_path / "tests.json").write_text('{"tests": []}', encoding="utf-8")
    shared = tmp_path / "data" / "retrieval"
    queries = [{"text": f"q{i}", "type": "partial", "intent": "lookup"} for i in range(3)]
    write(shared / "generate.ledger.jsonl", [
        {"seed": 4, "pending": True, "model": "sonnet"},
        {"seed": 4, "model": "sonnet", "prompt_version": "v", "repo": "here", "pages": ["a.md"], "queries": queries}])
    write(shared / "generate.runs.jsonl", [
        {"run": "t4-q0", "split": "validation", "claim": [claim("alpha")], "kinds": ["useful"]},
        {"run": "t4-q0", "split": "validation", "candidates": 0, "kinds": ["useful"]},
        {"run": "t4-q1", "split": "train", "claim": [claim("beta")], "kinds": ["useful"]},
        {"run": "t4-q2", "split": "validation", "claim": [claim("gamma")], "kinds": ["useful"]}])
    # A candidate already kept for the kind, in any file, is not cut again.
    gamma = [{"state": generate.view(n, "useful", claim("gamma")["state"]), "questions": {n: q}}
             for n, q in claim("gamma")["questions"].items()]
    write(tmp_path / "data" / "useful" / "v1-train.candidates.jsonl", gamma)

    out = generate.rebuild("useful", "validation", "full1-val")
    rows = read(tmp_path / "data" / "useful" / "full1-val.candidates.jsonl")
    # At most CAP a query, from validation's claims only (not beta's); gamma's are kept already.
    assert out == {"queries": 2, "kept": 2, "near_gate": 0, "near_kept": 2} and len(rows) == 2
    assert {r["provenance"]["query"] for r in rows} == {"q0"} and rows[0]["provenance"]["run"] == "t4-q0"
    assert all(len(r["state"]["passages"]) == 1 and r["provenance"]["type"] == "partial" for r in rows)
    with pytest.raises(SystemExit, match="exists"):
        generate.rebuild("useful", "validation", "full1-val")
