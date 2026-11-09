"""A pytest plugin the oneshot's contract runs load by name (`-p oneshot_probe`), never imported here.

`oneshot_build.probe` copies this file to a folder of its own and puts only that folder on
`PYTHONPATH`, so it runs inside the generated project's environment: the standard library and
pytest only. It writes JSON lines to `$ONESHOT_PROBE`:

- `{"kind": "item", "node", "name"}` for each collected test: pytest's own node id, never one
  rebuilt from a report, and the function's name without its parameter id.
- `{"kind": "report", "node", "when", "outcome", "entered", "intact", "missing_app", "text"}` for
  each phase that ran. `entered` says the test's own function was called; `intact` that nothing
  replaced how pytest runs a test; `missing_app` that the failure is `app` not being importable.

With `$ONESHOT_PROBE_NODES` naming a file of node ids, every other collected test is deselected.
"""

import functools
import inspect
import json
import os

import pytest

RUNTEST = pytest.Function.runtest


def _write(row: dict) -> None:
    with open(os.environ["ONESHOT_PROBE"], "a", encoding="utf-8") as out:
        out.write(json.dumps(row) + "\n")


def _missing_app(exc) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        name = getattr(exc, "name", None) or ""
        if isinstance(exc, ModuleNotFoundError) and (name == "app" or name.startswith("app.")):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def pytest_collection_modifyitems(config, items):
    wanted = os.environ.get("ONESHOT_PROBE_NODES")
    if not wanted:
        return
    with open(wanted, encoding="utf-8") as listed:
        keep = set(json.load(listed))
    config.hook.pytest_deselected(items=[i for i in items if i.nodeid not in keep])
    items[:] = [i for i in items if i.nodeid in keep]


RUNNERS: dict = {}   # each collected item type's own `runtest` as collection left it: unittest's is its own


def pytest_collection_finish(session):
    for item in session.items:
        RUNNERS.setdefault(type(item), type(item).runtest)
        _write({"kind": "item", "node": item.nodeid, "name": getattr(item, "originalname", None) or item.name})


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    func, entered = item.obj, []

    if inspect.iscoroutinefunction(func):   # an async test stays one, for the plugin that runs it
        @functools.wraps(func)
        async def body(*args, **kwargs):
            entered.append(True)
            return await func(*args, **kwargs)
    else:
        @functools.wraps(func)
        def body(*args, **kwargs):
            entered.append(True)
            return func(*args, **kwargs)

    item.obj = body
    try:
        yield
    finally:
        item.obj = func
        item.oneshot_entered = bool(entered)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    intact = pytest.Function.runtest is RUNTEST and type(item).runtest is RUNNERS.get(type(item)) \
        and "runtest" not in vars(item)
    _write({"kind": "report", "node": item.nodeid, "when": call.when, "outcome": report.outcome,
            "entered": bool(getattr(item, "oneshot_entered", False)), "intact": intact,
            "missing_app": call.excinfo is not None and _missing_app(call.excinfo.value),
            "text": (report.longreprtext or "")[-2000:]})
