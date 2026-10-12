"""Reliability PR 9 (`docs/plans/reliability/9-korean.md`, v1): the pair set,
the KO corpus, the checks that need no host, the paired gates and
attribution, the output overlay, and the translation-failure fixtures. No
network: Jev, the translator's transport and the host are stand-ins."""

import json
import re
import shutil
import threading
import time
from pathlib import Path

import pytest

import decision
import translate
from eval import compare, dataset, korean, meaning, report
from main import knowledge
from search import evidence

ROOT = Path(__file__).resolve().parents[1]
V8 = ROOT / "eval" / "jev" / "reliability-v8" / "intents.json"
ACTIVE = decision.Config("active", "jev-1.13.0", "file", key="k")

PORTS_KO = "# 네트워크 포트\n\n수집 게이트웨이는 7443 포트에서 수신한다. 메트릭 익스포터는 9102 포트에서 수신한다.\n"
SIGN_KO = ("---\nsupersedes: [2025-02-11-084-docusign]\n---\n\n# Yousign 전자서명\n\n결정. 계약은 이제 Yousign 에서 "
           "서명한다. DocuSign 계정은 2026-06-30 에 닫혔다.")
# The stand-in translator: each Korean phrase it knows, in English.
ENGLISH = {"네트워크 포트": "Network ports", "Yousign 전자서명": "E-signatures in Yousign",
           "수집 게이트웨이는 7443 포트에서 수신한다.": "The ingest gateway listens on port 7443.",
           "메트릭 익스포터는 9102 포트에서 수신한다.": "The metrics exporter listens on port 9102.",
           "결정.": "Decision.", "계약은 이제 Yousign 에서 서명한다.": "Contracts are now signed in Yousign.",
           "DocuSign 계정은 2026-06-30 에 닫혔다.": "The DocuSign account was closed on 2026-06-30.",
           "Kestrel 수집 게이트웨이는 어느 포트에서 수신하나요?": "Which port does the Kestrel ingest gateway listen on?",
           "지금 어떤 전자서명 도구를 쓰나요? 예전 계정은 언제 닫혔나요?":
               "Which e-signature tool do we use now? When was the old account closed?",
           "오류 예산": "Error budgets",
           "사용자는 오류 예산을 백분율이 아니라 그달의 남은 분으로 보여 주기를 원한다.":
               "The user wants error budgets shown as remaining minutes for the month, not as percentages.",
           "사용자는 보고서에서 오류 예산을 어떻게 보여 주길 원했나요?":
               "How did the user want error budgets shown in reports?"}
BUDGETS_KO = "# 오류 예산\n\n사용자는 오류 예산을 백분율이 아니라 그달의 남은 분으로 보여 주기를 원한다."
BUDGETS = "repo/.wiki/memory/2026-10-02-error-budgets.md"


@pytest.fixture
def small(monkeypatch):
    """The test pair files as a whole set: their groups, one pair each."""

    monkeypatch.setattr(korean, "GROUPS", ("conflict", "names", "negation"))
    monkeypatch.setattr(korean, "PER_GROUP", 1)


def pair_file(tmp_path: Path, memory: bool = False, **change) -> Path:
    """Two pairs over v8 — a name and a conflict — with their twins, and the gates beside them."""

    pairs = {"schema": korean.SCHEMA, "version": 1,
             "dataset": {"path": "eval/jev/reliability-v8/intents.json", "sha256": dataset.sha(V8.read_bytes())},
             "labels": {"reviewed_by": "gpt-6.1-sol", "reviewed_at": "2026-10-13",
                        "reviews": {"k1": {"kind": "model", "by": "gpt-6.1-sol", "at": "2026-10-13"}}},
             "pairs": [{"intent": "fact-01", "group": "names", "input": {"ko": list(ENGLISH)[7]},
                        "meaning": {"require": [["port"]], "forbid": [], "literals": ["Kestrel"]}, "review": "k1"},
                       {"intent": "conflict-43", "group": "conflict", "input": {"ko": list(ENGLISH)[8]},
                        "meaning": {"require": [["now"], ["closed"]], "forbid": [], "literals": []}, "review": "k1"}],
             "twins": {"repo/docs/ports.md": {"text": PORTS_KO, "headings": {"Network ports": "네트워크 포트"},
                                              "facts": [{"id": "ports-1", "section": "Network ports", "sentence": 0,
                                                         "require": [["ingest gateway"], ["listens"]],
                                                         "literals": ["7443"]}]},
                       "repo/.wiki/decisions/2026-05-04-085-yousign.md": {
                           "text": SIGN_KO, "headings": {"E-signatures in Yousign": "Yousign 전자서명"},
                           "facts": [{"id": "sign-1", "section": "E-signatures in Yousign", "sentence": 2,
                                      "require": [["closed"]], "literals": ["2026-06-30"]}]}}}
    if memory:
        # A private source: its English lives in the index's evidence store, never the translator's cache.
        pairs["pairs"].append({"intent": "memory-43", "group": "negation", "input": {"ko": list(ENGLISH)[11]},
                               "meaning": {"require": [["error budget"]], "forbid": [], "literals": []},
                               "review": "k1"})
        pairs["twins"][BUDGETS] = {"text": BUDGETS_KO, "headings": {"Error budgets": "오류 예산"},
                                   "facts": [{"id": "budgets-1", "section": "Error budgets", "sentence": 0,
                                              "require": [["minutes"], ["not as percent"]], "literals": []}]}
    for key, value in change.items():
        pairs[key] = value
    folder = tmp_path / "korean"
    folder.mkdir(exist_ok=True)
    shutil.copy(ROOT / "eval" / "jev" / "korean" / "gates.json", folder / "gates.json")
    path = folder / "pairs.json"
    path.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def v8():
    return dataset.load(V8)


# -- the set --------------------------------------------------------------------------

def test_a_pair_file_validates_and_counts_its_groups(tmp_path, v8):
    path = pair_file(tmp_path)
    # Two pairs are not the frozen cohort: the run and the report refuse them.
    with pytest.raises(ValueError, match="pairs, not 12"):
        korean.load(path)
    pairs = json.loads(path.read_text(encoding="utf-8"))
    data = v8
    assert korean.invalid(pairs, data) == []
    assert korean.allocation(pairs, data) == [f"{g}: {int(g in ('names', 'conflict'))} pairs, not 12"
                                              for g in korean.GROUPS]
    assert korean.calibration(pairs, data) == {**dict.fromkeys(korean.GROUPS, 0), "names": 1}


def test_an_ineligible_or_ill_formed_pair_is_named(tmp_path, v8):
    pairs = json.loads(pair_file(tmp_path).read_text(encoding="utf-8"))
    pairs["pairs"].append({"intent": "direct-45", "group": "multipart", "input": {"ko": "하루는 몇 초인가?"},
                           "meaning": {"require": [["seconds"]], "forbid": [], "literals": []}, "review": "k1"})
    pairs["pairs"][0]["input"]["ko"] = "Which port?"
    pairs["pairs"][1]["meaning"] = {"required": [["now"]], "excluded": []}
    pairs["twins"]["repo/docs/ports.md"]["headings"] = {"Ports": "네트워크 포트"}
    problems = korean.invalid(pairs, v8)
    assert "direct-45: a pair's intent has evidence and parts, and no fault" in problems
    assert "fact-01: input.ko is a Korean wording" in problems
    assert any(p.startswith("conflict-43: meaning is") for p in problems)
    assert any(p.startswith("repo/docs/ports.md: sections") for p in problems)
    del pairs["twins"]["repo/docs/ports.md"]
    assert "fact-01: repo/docs/ports.md has no twin" in korean.invalid(pairs, v8)


def test_a_fact_section_must_be_the_only_one_of_its_name():
    english = "# Install\n\n## Windows\n\n### Setup\n\nNever use customer passwords.\n\n## Linux\n\n### Setup\n\nRun it."
    twin = {"text": "# 설치\n\n## Windows\n\n### 설정\n\n고객 비밀번호를 쓰지 않는다.\n\n## Linux\n\n### 설정\n\n실행한다.",
            "headings": {"Install": "설치", "Windows": "Windows", "Linux": "Linux", "Setup": "설정"},
            "facts": [{"id": "s-1", "section": "Setup", "sentence": 0, "require": [["never"]], "literals": []}]}
    assert korean.mapped(english, twin) == ["two sections named 'Setup'", "two sections named '설정'"]


def test_the_ko_corpus_swaps_only_the_twins_and_its_labels_map_back(tmp_path, v8, small):
    pairs, data = korean.load(pair_file(tmp_path, memory=True))
    ko = korean.ko_corpus(data, pairs)
    changed = {n for n, t in dataset.pages(ko).items() if dataset.pages(data)[n] != t}
    assert changed == set(pairs["twins"]) and data["corpus"]["files"]["repo/docs/ports.md"] != PORTS_KO
    label = korean.relabel(pairs)
    assert label("repo/docs/ports.md#네트워크 포트") == "repo/docs/ports.md#Network ports"
    assert label("repo/docs/other.md#네트워크 포트") == "repo/docs/other.md#네트워크 포트"
    assert dataset.recall(next(i for i in data["intents"] if i["id"] == "fact-01"),
                          [label("repo/docs/ports.md#네트워크 포트")]) == 1.0


# -- the meaning checks ---------------------------------------------------------------------

def test_a_negation_must_stay_in_its_own_sentence():
    require = [["does not run", "doesn't run"], ["windows"]]
    assert korean.lost("It does not run on Windows. It runs on Linux.", require, []) == []
    # Both words survive, in different sentences: the meaning flipped and `translate.kept` would not see it.
    assert korean.lost("It runs on Windows. It does not run on Linux.", require, [])
    assert korean.lost("Port 7443 is used.", [], ["7443"]) == []
    assert korean.lost("Port 7444 is used.", [], ["7443"]) == ["lost literal '7443'"]
    # A soft line break is not a sentence's end; a blank line, a heading or a list item is.
    assert korean.lost("It does not run\non Windows.", require, []) == []
    assert korean.lost("It does not run\n\non Windows.", require, [])
    assert korean.lost("- It does not run\n- on Windows", require, [])
    assert korean.sentences("# Title\nBody one. Body\ntwo.\n| a | b |") == ["# Title", "Body one.", "Body two.",
                                                                         "| a | b |"]


def test_a_literal_is_a_whole_token():
    assert korean.lost("The timeout is 300 seconds.", [], ["30"]) == ["lost literal '30'"]
    assert korean.lost("It costs 1,300 EUR or 3.05 units.", [], ["30"]) and korean.lost("PRs.", [], ["PR"])
    assert korean.lost("Wait 30 seconds, not 300.", [], ["30"]) == []
    assert korean.lost("The rate was 4.1%.", [], ["4.1%"]) == [] and korean.lost("See [a](b.md).", [], ["[a](b.md)"]) == []


def test_meaning_check_reads_literals_exactly():
    case = {"require": [["retry"]], "forbid": [], "literals": ["30"]}
    assert meaning.check(case, {"status": "translated", "text": "Retry after 30 seconds."}) == []
    assert meaning.check(case, {"status": "translated", "text": "Retry after 20 seconds."}) == ["lost literal '30'"]
    assert meaning.check({"require": [], "forbid": []}, {"status": "translated", "text": "x"}) == []
    assert meaning.check(case, {"status": "translated", "text": "Retry after 300 seconds."}) == ["lost literal '30'"]


def test_fact_units_are_checked_whether_or_not_retrieval_reaches_them(tmp_path, small):
    pairs, _data = korean.load(pair_file(tmp_path, memory=True))
    del pairs["twins"][BUDGETS]
    chunks = [{"label": "repo/docs/ports.md#네트워크 포트", "text": PORTS_KO},
              {"label": "repo/.wiki/decisions/2026-05-04-085-yousign.md#Yousign 전자서명", "text": SIGN_KO}]

    def english(texts: dict[str, str | None]):
        return lambda got: [{"status": "translated", "text": texts[c["label"]]} if texts[c["label"]] else
                            {"status": "unavailable", "text": None, "reason": "deadline"} for c in got]

    good = {chunks[0]["label"]: "The ingest gateway listens on port 7443. The exporter listens on 9102.",
            chunks[1]["label"]: "Decision. Contracts are signed in Yousign. The account was closed on 2026-06-30."}
    got = korean.facts(pairs, chunks, lambda c: c["label"], english(good))
    assert [f["status"] for f in got] == ["held", "held"]
    flipped = {**good, chunks[0]["label"]: "The ingest gateway is not used. Something listens on port 7443."}
    assert [f["status"] for f in korean.facts(pairs, chunks, lambda c: c["label"], english(flipped))] == \
        ["held", "lost"]
    failed = {**good, chunks[1]["label"]: None}
    assert [(f["status"], f["missing"]) for f in korean.facts(pairs, chunks, lambda c: c["label"], english(failed))] \
        == [("not_normalized", ["deadline"]), ("held", [])]
    assert korean.facts(pairs, chunks[:1], lambda c: c["label"], english(good))[0]["status"] == "not_normalized"


# -- the paired gates and attribution ----------------------------------------------------------

def world(n: int):
    """`n` synthetic pairs over one-group, one-part intents."""

    intents = [{"id": f"p{i:02}", "evidence": [[f"repo/docs/p{i:02}.md"]], "parts": [{"id": "q0"}], "abstain": False,
                "direct": False, "category": "factual", "analysis": False} for i in range(n)]
    data = {"intents": intents, "labels": {"reviewed_by": "gpt-6.1-sol"}}
    pairs = {"labels": {"reviewed_by": "gpt-6.1-sol"},
             "pairs": [{"intent": i["id"], "meaning": {"require": [["port"]], "forbid": [], "literals": []}}
                       for i in intents]}
    return data, pairs


def row(intent: str, arm: str, rep: int = 0, found: bool = True, covered: str = "answered", **extra) -> dict:
    out = {"key": f"{intent}:{arm}:{rep}", "intent": intent, "arm": arm, "rep": rep, "status": "ready",
           "reason": "covered", "direct": False, "sources": [], "rejected": [], "leaks": [], "breaches": [],
           "beyond_k": [], "elapsed_ms": 10, "transitions": ["route", "retrieve", "ready"],
           "evidence": [f"repo/docs/{intent}.md#H"] if found else [],
           "normalization": {"query": "translated" if arm in "BD" else "original_english"},
           "question_en": "Which port?", "answer": {"status": "answered", "verified": True, "fabricated": []},
           "grade": {"parts": {"q0": covered}, "claims": [{"supported": True}]}, "cost": {"host_usd": 0.08}}
    return {**out, **extra}


def every(data, **by_key) -> list[dict]:
    return [by_key.get(f"{i['id']}:{a}") or row(i["id"], a) for i in data["intents"] for a in korean.ARMS]


CFG = json.loads((ROOT / "eval" / "jev" / "korean" / "gates.json").read_text(encoding="utf-8"))


def verdicts(got: dict) -> dict:
    return {g["id"]: g["verdict"] for g in got["gates"]}


def test_eighty_four_pairs_tolerate_one_lost_pair_and_not_two():
    data, pairs = world(84)
    one = korean.paired_report(every(data, **{"p00:C": row("p00", "C", found=False)}), data, pairs, [], 8, CFG)
    assert one["differences"]["recall_C_minus_A"]["low"] == -0.0357 and verdicts(one)["paired_recall"] == "pass"
    two = korean.paired_report(every(data, **{f"p0{i}:C": row(f"p0{i}", "C", found=False) for i in (0, 1)}),
                               data, pairs, [], 8, CFG)
    assert two["differences"]["recall_C_minus_A"]["low"] == -0.0595 and verdicts(two)["paired_recall"] == "fail"
    # A failed retrieval recalls nothing, whatever evidence its fallback kept.
    failed = korean.paired_report(every(data, **{f"p0{i}:C": row(f"p0{i}", "C", status="unavailable") for i in (0, 1)}),
                                  data, pairs, [], 8, CFG)
    assert failed["failed_retrieval"] == ["p00:C", "p01:C"] and verdicts(failed)["paired_recall"] == "fail"
    assert verdicts(one)["literals"] == "pass" and verdicts(one)["integrity"] == "pass"


def test_a_missing_row_or_grade_never_shrinks_the_cohort():
    data, pairs = world(84)
    rows = every(data)
    gone = [r for r in rows if r["key"] != "p03:B:0"]
    got = korean.paired_report(gone, data, pairs, [], 8, CFG)
    assert got["missing"] == ["p03:B"] and verdicts(got)["paired_recall"] == "inconclusive"
    assert verdicts(got)["paired_coverage"] == "inconclusive" and verdicts(got)["integrity"] == "inconclusive"
    # A failed answer covers nothing; it is counted, not dropped.
    failed = every(data, **{"p03:D": {**row("p03", "D", answer=None), "answer_error": "RuntimeError: host"}})
    got = korean.paired_report(failed, data, pairs, [], 8, CFG)
    assert got["failed_answers"] == ["p03:D:0"] and got["differences"]["coverage_D_minus_A"]["n"] == 84
    # A grade still missing after its regrade is unscorable: coverage is not settled, nor a verified answer's support.
    ungraded = every(data, **{"p03:C": row("p03", "C", grade={"error": "unparsed"})})
    got = korean.paired_report(ungraded, data, pairs, [], 8, CFG)
    assert got["unscorable"] == ["p03:C"] and verdicts(got)["paired_coverage"] == "inconclusive"
    assert verdicts(got)["paired_recall"] == "pass" and got["integrity"]["ungraded_verified"] == ["p03:C:0"]
    assert verdicts(got)["integrity"] == "inconclusive"


def test_literals_and_integrity_fail_on_one_loss_and_wait_on_what_was_not_checked():
    data, pairs = world(4)
    lost = every(data, **{"p01:B": row("p01", "B", question_en="Which gate?")})
    assert verdicts(korean.paired_report(lost, data, pairs, [], 8, CFG))["literals"] == "fail"
    unnormalized = every(data, **{"p01:D": row("p01", "D", normalization={"query": "unavailable"}, question_en=None)})
    assert verdicts(korean.paired_report(unnormalized, data, pairs, [], 8, CFG))["literals"] == "inconclusive"
    assert verdicts(korean.paired_report(every(data), data, pairs, None, 8, CFG))["literals"] == "inconclusive"
    assert verdicts(korean.paired_report(every(data), data, pairs, [{"page": "x", "status": "lost"}], 8,
                                         CFG))["literals"] == "fail"
    fabricated = every(data, **{"p02:C": row("p02", "C", answer={"status": "answered", "verified": True,
                                                                  "fabricated": ["repo/docs/x.md:1"]})})
    assert verdicts(korean.paired_report(fabricated, data, pairs, [], 8, CFG))["integrity"] == "fail"
    unsupported = every(data, **{"p02:A": row("p02", "A", grade={"parts": {"q0": "answered"},
                                                                 "claims": [{"supported": False}]})})
    assert verdicts(korean.paired_report(unsupported, data, pairs, [], 8, CFG))["integrity"] == "fail"
    unreviewed = {**pairs, "labels": {"reviewed_by": None}}
    assert verdicts(korean.paired_report(every(data), data, unreviewed, [], 8, CFG))["paired_recall"] == "provisional"


def test_divergent_and_boundary_pairs_are_planned_with_their_cost_and_a_second_approval():
    data, pairs = world(4)
    rows = every(data, **{"p00:C": row("p00", "C", found=False),
                          "p01:A": row("p01", "A", covered="partial"),
                          "p02:B": row("p02", "B", question_en="Which gate?")})
    rep = korean.paired_report(rows, data, pairs, [], 8, CFG)["repetition"]
    assert rep["divergent"] == {"p00": ["C"], "p01": ["B", "C", "D"], "p02": ["B"]}
    assert rep["boundary"] == ["p01"]
    # A meaning loss needs no repetition; A's two runs are shared by the pair's arms.
    assert rep["plan"] == {"p00": ["C"], "p01": ["B", "C", "D"]}
    assert rep["rows"] == 2 * 2 + 2 * 4 and rep["host_usd"] == round(12 * 0.08, 2)


def test_attribution_follows_the_frozen_order():
    data, pairs = world(6)
    base = every(data, **{"p00:A": row("p00", "A", found=False), "p00:C": row("p00", "C"),
                          "p01:B": row("p01", "B", question_en="Which gate?"),
                          **{f"p0{i}:C": row(f"p0{i}", "C", found=False) for i in (2, 3, 4, 5)}})
    again = [row("p03", "A", rep) for rep in (1, 2)] + [row("p03", "C", 1), row("p03", "C", 2)] + \
            [row("p04", "A", rep) for rep in (1, 2)] + [row("p04", "C", r, found=False) for r in (1, 2)] + \
            [row("p05", "A", 1), row("p05", "C", 1, found=False)]
    got = korean.paired_report(base, data, pairs, [], 8, CFG, repeats=again)["attribution"]
    # A failed in p00, so every arm differs from it there, and each is A's failure.
    assert got == {**{f"p00:{x}": "controller_or_baseline" for x in "BCD"}, "p01:B": "translation_defect",
                   "p02:C": "unresolved_not_repeated", "p03:C": "variation", "p04:C": "evidence_or_index",
                   "p05:C": "unresolved_missing_repetition"}
    # Without repetitions the report plans them and attributes nothing.
    assert korean.paired_report(base, data, pairs, [], 8, CFG)["attribution"] is None


# -- the output overlay ---------------------------------------------------------------------

def test_the_overlay_splits_as_the_screen_does_and_finds_what_a_rendering_broke():
    answer = "Port 7443 serves it [repo/docs/ports.md].\n\n```\nrun x\n\nrun y\n```\n\n- one\n- two"
    assert korean.paragraphs(answer) == ["Port 7443 serves it [repo/docs/ports.md].", "```\nrun x\n\nrun y\n```",
                                         "- one\n- two"]
    kept = korean.overlaid(answer, [("포트 7443 이 [repo/docs/ports.md] 를 맡는다.", "translated"),
                                    ("```\nrun x\n\nrun y\n```", "skipped"), ("- 하나\n- 둘", "translated")])
    assert kept["broken"] == [] and kept["refused"] == 0
    broken = korean.overlaid(answer, [("포트가 그것을 맡는다.", "translated"), ("```\nrun x\n```", "skipped"),
                                      ("하나, 둘", "translated")])
    assert broken["broken"] == [0, 1, 2]
    refused = korean.overlaid(answer, [("Port 7443 serves it [repo/docs/ports.md].", "meaning_changed"),
                                       ("```\nrun x\n\nrun y\n```", "skipped"), ("- 하나\n- 둘", "translated")])
    assert refused["refused"] == 1 and refused["broken"] == []
    # A table that lost a column, or a paragraph never returned, is broken.
    table = "| name | value |\n| --- | --- |\n| retry | 30 |"
    merged = korean.overlaid(table, [("| 이름 값 |\n| --- |\n| retry 30 |", "translated")])
    assert merged["broken"] == [0] and merged["detail"][0]["problems"] == ["table or list layout changed"]
    assert korean.overlaid(answer, [("포트 7443 이 [repo/docs/ports.md] 를 맡는다.", "translated")])["broken"] == [1, 2]


def test_a_batch_records_its_own_spending_when_two_share_a_ceiling(tmp_path):
    ceiling, run = compare.Ceiling(60, 10, 10_000), {"batches": []}
    (tmp_path / "x").mkdir()
    for key, usd in (("en", 0.10), ("ko", 0.20)):
        compare.batch(tmp_path / "x", run, "pairs", [{"key": key}], lambda u, usd=usd: {"key": u["key"],
                                                                                       "cost": {"host_usd": usd}},
                      ceiling)
    assert [(b["usd"], b["rows"], b["left"]) for b in run["batches"]] == [(0.1, 1, 0), (0.2, 1, 0)]


def test_a_repetition_must_repeat_its_base_measurement():
    base = {"pairs": {"sha256": "a"}, "corpora": {"KO": "k"}, "k": 8, "reps": [0], "plan": None}
    assert korean.differ({**base, "reps": [1, 2], "plan": {"p": ["C"]}, "base": "run"}, base) == []
    assert korean.differ({**base, "pairs": {"sha256": "b"}, "k": 1, "corpora": {"KO": "x"}}, base) == \
        ["corpora", "k", "pairs"]


def test_the_question_outcome_is_the_measured_prepare_not_the_priming_one():
    seen = [("q", {"status": "translated", "cached": False}), ("q", {"status": "translated", "cached": True})]
    assert korean.translation("q", seen)["question"]["cached"] is True


# -- translation failure: deterministic fixtures, no network ---------------------------------------

SOURCE = "`tool/x.py` 는 포트 7443 을 쓴다."


def transport(kind: str):
    """The translator's HTTP transport failing as `kind` says (under `translate._ask`)."""

    class Reply:
        def __init__(self, items):
            self.body = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}],
                                    "usageMetadata": {}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def urlopen(request, timeout):
        if kind == "raises":
            raise OSError("connection refused")
        if kind == "slow":
            time.sleep(1.5)
        batch = json.loads(json.loads(request.data)["contents"][0]["parts"][0]["text"])
        if kind == "count":
            return Reply(["The tool uses port 7443."] * (len(batch) + 1))
        if kind == "spans":
            return Reply([translate.TOKEN.sub("", item).replace("는 포트 7443 을 쓴다.", "uses port 7443.")
                          for item in batch])
        return Reply([item.replace("는 포트 7443 을 쓴다.", " uses port 7443.") for item in batch])

    return urlopen


FAULTS = {"raises": "request_failed", "limit": None, "slow": None, "count": "request_failed",
          "spans": "spans_broken"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "CACHE", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(translate, "ENV", tmp_path / "absent.env")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.delenv("TRANSLATE_MONTHLY_USD", raising=False)
    before = set(threading.enumerate())
    yield monkeypatch
    for worker in set(threading.enumerate()) - before:
        worker.join(5)


@pytest.mark.parametrize("kind", list(FAULTS))
def test_a_translator_fault_ends_explicit_with_no_english_in_evidence_and_question(isolated, kind):
    sent = []

    def urlopen(request, timeout):
        sent.append(True)
        return transport(kind)(request, timeout)

    isolated.setattr(translate.urllib.request, "urlopen", urlopen)
    if kind == "limit":
        isolated.setenv("TRANSLATE_MONTHLY_USD", "0")
    # Evidence normalization, as `judge` asks for a passage.
    out = knowledge.english([SOURCE], 0.4)[0]
    assert out["status"] not in evidence.USABLE and out["text"] is None, out
    if FAULTS[kind]:
        assert out["reason"] == FAULTS[kind]
    assert (not sent) if kind == "limit" else sent
    # The question, through `prepare`'s normalize state: no English, so no Jev, and the fallback says why.
    from test_evidence import jev

    seen = []
    jev(isolated, [], seen, answer=lambda *a: pytest.fail("Jev was asked"))
    isolated.setattr(knowledge, "NORMALIZE_SECONDS", 0.4)
    d = knowledge.prepare(SOURCE.replace("쓴다.", "쓰나?"), None, cfg=ACTIVE, cache=None)
    assert d["status"] == "unavailable" and d["reason"] == "normalization_failed" and d["question_en"] is None
    assert d["normalization"]["query"] not in evidence.USABLE and seen == []


def test_the_working_transport_does_translate(isolated):
    isolated.setattr(translate.urllib.request, "urlopen", transport("ok"))
    out = knowledge.english([SOURCE], 5.0)[0]
    assert out["status"] == "translated" and "7443" in out["text"] and "`tool/x.py`" in out["text"]


# -- one pairs run, end to end, with stand-ins ----------------------------------------------------

@pytest.fixture
def ran(tmp_path, monkeypatch, small):
    from test_decision_flow import answering

    path = pair_file(tmp_path, memory=True)
    env = tmp_path / "jev.env"
    env.write_text("TYPESAFE_API_KEY=test-key\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))

    def evaluate(cfg, state, questions, trace, budget=None, stage=""):
        budget.call()
        trace.append({"stage": stage, "model": cfg.model, "usage": {"input_tokens": 1, "output_tokens": 1}})
        return answering()(stage, state, questions)

    monkeypatch.setattr(decision, "evaluate", evaluate)
    monkeypatch.setattr(translate, "CACHE", tmp_path / "translate.sqlite3")
    monkeypatch.setattr(translate, "api_key", lambda: "test-key")

    def ask(system, batch, seconds, same_length=True):
        out = []
        for item in batch:
            for ko, en in sorted(ENGLISH.items(), key=lambda x: -len(x[0])):
                item = item.replace(ko, en)
            out.append(item)
        return out

    monkeypatch.setattr(translate, "_ask", ask)
    graded_once = set()

    def graded(unit, d, answer, data, model):
        if unit["key"] == "conflict-43:D:0" and unit["key"] not in graded_once:
            graded_once.add(unit["key"])
            return {"error": "unparsed", "reply": "", "host_usd": 0.01}
        return {"parts": {p["id"]: "answered" for p in unit["intent"]["parts"]}, "claims": [{"supported": True}],
                "host_usd": 0.01}

    monkeypatch.setattr(compare, "answered", lambda unit, d, repo, jev, cfg, model: {
        "status": "verified", "text": "It is 7443.", "verified": True, "fabricated": [], "host_usd": 0.02,
        "host_turns": 1, "elapsed_ms": 3})
    monkeypatch.setattr(compare, "graded", graded)
    folder = tmp_path / "run"
    args = [str(folder), "--experiment", "pairs", "--pairs", str(path), "--method", "bm25"]
    assert compare.main(args) == 0
    return folder, args


def test_a_pairs_run_records_both_corpora_the_fact_pass_and_reports_its_gates(ran):
    folder, args = ran
    rows = compare.rows(folder, "pairs")
    assert sorted(r["key"] for r in rows) == sorted(f"{i}:{a}:0" for i in ("conflict-43", "fact-01", "memory-43")
                                                    for a in "ABCD")
    assert {r["arm"]: r["corpus"] for r in rows} == {"A": "EN", "B": "EN", "C": "KO", "D": "KO"}
    # A KO twin's chunk is named by its English heading, as the labels are; no Korean heading is a label.
    sign = next(r for r in rows if r["key"] == "conflict-43:D:0")
    assert "repo/.wiki/decisions/2026-05-04-085-yousign.md#E-signatures in Yousign" in sign["evidence"]
    assert not any(re.search("[가-힣]", e) for r in rows for e in r["evidence"] + r["beyond_k"])
    asked = {r["arm"]: r["translation"]["question"]["status"] for r in rows if r["intent"] == "fact-01"}
    assert asked == {"A": "original_english", "B": "translated", "C": "original_english", "D": "translated"}
    d = next(r for r in rows if r["key"] == "fact-01:D:0")
    assert d["translation"]["question"]["cached"] and d["translation"]["question"]["model"] == translate.MODEL
    assert next(r for r in rows if r["key"] == "conflict-43:D:0")["grade_turns"] == 2
    facts = json.loads((folder / "facts.json").read_text(encoding="utf-8"))["facts"]
    assert [(f["id"], f["status"]) for f in facts] == [("sign-1", "held"), ("budgets-1", "held"), ("ports-1", "held")]
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert set(run["options"]["corpora"]) == {"EN", "KO"} and run["options"]["inputs"]["C"] == ["en", "KO"]
    assert run["manifest"]["gates"].endswith("korean/gates.json")
    got = report.build([folder])
    judged = {g["id"]: g["verdict"] for g in got["gates"]}
    assert (judged["literals"], judged["integrity"], judged["paired_coverage"]) == ("pass", "pass", "pass")
    # BM25 over an English question does not reach a Korean twin: the recall it loses is measured, not hidden.
    lows = [got["pairs"]["differences"][f"recall_{a}_minus_A"]["low"] for a in "BCD"]
    assert judged["paired_recall"] == ("pass" if min(lows) >= -0.05 else "fail")
    assert got["pairs"]["missing"] == [] and got["pairs"]["drifted"] == []
    assert set(got["pairs"]["repetition"]["divergent"]) <= {"fact-01", "conflict-43", "memory-43"}
    assert "Pairs cohort: 12 rows" in report.markdown(got)
    # Resuming records nothing twice.
    assert compare.main(args) == 0 and len(compare.rows(folder, "pairs")) == 12


def test_a_repetition_runs_and_is_read_only_as_its_base_measurement(ran, tmp_path):
    folder, args = ran
    made = tmp_path / "report.json"
    made.write_text(json.dumps(report.build([folder]), ensure_ascii=False), encoding="utf-8")
    again = tmp_path / "again"
    with pytest.raises(SystemExit, match=r"measured something else \(k\)"):
        compare.main([str(again), *args[1:], "--plan", str(made), "--k", "4"])
    assert compare.main([str(again), *args[1:], "--plan", str(made)]) == 0
    run = json.loads((again / "run.json").read_text(encoding="utf-8"))
    assert run["options"]["reps"] == [1, 2] and run["options"]["base"] == folder.as_posix()
    assert report.build([folder, again])["pairs"]["attribution"] is not None
    run["options"]["k"] = 4
    (again / "run.json").write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"does not repeat .* \(k\)"):
        report.build([folder, again])


def test_a_resumed_batch_reads_the_private_english_that_was_checked(ran, monkeypatch):
    folder, args = ran
    kept = json.loads((folder / "private.json").read_text(encoding="utf-8"))["english"]
    sentence = list(ENGLISH)[10]
    assert any(sentence in text for text in kept)
    # The translator would now say otherwise; a new batch's scratch store must not ask it.
    monkeypatch.setattr(translate, "_ask", lambda system, batch, seconds, same_length=True: [
        "The user wants error budgets as a percentage." if sentence in item else item for item in batch])
    before = compare.rows(folder, "pairs")
    (folder / "pairs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in before
                                                if r["key"] != "memory-43:D:0"), encoding="utf-8")
    assert compare.main(args) == 0
    again = next(r for r in compare.rows(folder, "pairs") if r["key"] == "memory-43:D:0")
    checked = json.loads((folder / "facts.json").read_text(encoding="utf-8"))["english"]
    read = again["translation"]["evidence"]["read"]
    assert set(read) & set(checked) and report.build([folder])["pairs"]["drifted"] == []
    # Without the run's private English the batch normalizes afresh, and the report sees the drift.
    (folder / "private.json").unlink()
    (folder / "pairs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in before
                                                if r["key"] != "memory-43:D:0"), encoding="utf-8")
    assert compare.main(args) == 0
    got = report.build([folder])
    assert got["pairs"]["drifted"] == ["memory-43:D:0"]
    assert {g["id"]: g["verdict"] for g in got["gates"]}["literals"] == "inconclusive"
