"""The gate runs that reached each outside call (`oneshot_calls`)."""

from __future__ import annotations

from main import oneshot_calls as calls
from main import oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

KEY = {"id": "N1", "what": "번역 API 키", "kind": "secret", "env": "TRANSLATE_KEY"}
WEB = {"api": [{"id": "P1"}, {"id": "P2"}],
       "outside": [{"id": "X1", "target": "번역 API", "depends_on": ["R1", "P2"], "needs": [KEY]},
                   {"id": "X2", "target": "공개 환율 페이지", "depends_on": ["R1", "P1"], "needs": []}],
       "tests": [{"test_name": "test_translates", "depends_on": ["R1", "P2"]},
                 {"test_name": "test_rates", "depends_on": ["R1", "P1"]},
                 {"test_name": "test_lists", "depends_on": ["R1"]}]}


def test_a_test_reaches_the_call_its_endpoint_or_any_module_on_the_way_makes():
    assert calls.reaching(WEB) == {"X1": ["test_translates"], "X2": ["test_rates"]}
    modules = {"modules": [{"id": "M1", "calls": ["M2"]}, {"id": "M2", "calls": ["X1"]}, {"id": "M3", "calls": []}],
               "outside": [{"id": "X1", "depends_on": ["R1"]}],
               "tests": [{"test_name": "test_top", "depends_on": ["R1", "M1"]},
                         {"test_name": "test_alone", "depends_on": ["R1", "M3"]},
                         {"test_name": "test_call", "depends_on": ["X1"]}]}
    assert calls.reaching(modules) == {"X1": ["test_top", "test_call"]}


def test_each_report_counts_once_toward_the_calls_its_tests_ran_and_never_for_a_skip(world, tmp_path):  # noqa: F811
    oneshot_spec.saved("budget", oneshot={"design": WEB})
    spec = oneshot_spec.spec_of("budget")
    calls.reached(spec, [("t", "test_translates[ko]", "", "failure"), ("t", "test_translates[en]", "", "passed"),
                         ("t", "test_rates", "", "skipped")])
    calls.reached(spec, [("t", "test_lists", "", "passed")])
    report = tmp_path / "junit.xml"
    calls.ran(spec, report)   # a gate that wrote no report counts nothing
    report.write_text('<testsuite><testcase classname="t" name="test_translates"/>'
                      '<testcase classname="t" name="test_rates"/></testsuite>', encoding="utf-8")
    calls.ran(spec, report)
    spec = oneshot_spec.spec_of("budget")
    assert spec["oneshot"]["calls"] == {"X1": 2, "X2": 1}
    # Only a call that needs a secret lists the tests CI skips for it.
    assert calls.summary(spec) == [{"id": "X1", "target": "번역 API", "runs": 2, "tests": ["test_translates"]},
                                   {"id": "X2", "target": "공개 환율 페이지", "runs": 1, "tests": []}]
