"""Every test runs against an empty home, never the machine's user-level hooks.

Set in the environment rather than patched on the module, so the installers
the tests start as subprocesses see the same empty home.
"""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    full = any(Path(arg.split("::", 1)[0]).resolve() == Path(__file__).resolve().parent for arg in config.args)
    focused = config.option.keyword or config.option.markexpr or config.getoption("lf", False)
    return min(8, os.cpu_count() or 1) if full and not focused else 0


# Windows applications can export TMPDIR pointing at their own shared scratch
# folder. Keep test Git repositories in the user's native temporary directory;
# pytest's explicit --basetemp still takes precedence for its fixtures.
if os.name == "nt" and os.environ.get("TEMP"):
    os.environ["TMPDIR"] = os.environ["TEMP"]
    tempfile.tempdir = None

os.environ["WIKI_USER_HOME"] = tempfile.mkdtemp(prefix="wiki-home-")

# `search.ask` reaches the daemon only when this is unset. A test that spawned
# one would start a real daemon and download its model.
os.environ["WIKI_SEARCH"] = "off"

# No key and no seeded cache: opening a pull request translates it, and with
# the checkout's `.env` every such test spent a live Gemini request.
_scratch = tempfile.mkdtemp(prefix="wiki-translate-")
os.environ["TRANSLATE_ENV"] = os.path.join(_scratch, "absent.env")
os.environ["TRANSLATE_CACHE"] = os.path.join(_scratch, "cache.sqlite3")
# An absent file still falls back to the inherited machine key.
os.environ.pop("GEMINI_API_KEY", None)

# The same for Jev: the hub's `.env` holds a live TypeSafe key, and a machine
# may export one too. No test reads either; a test that wants a key writes
# its own file and points `JEV_ENV` at it.
os.environ["JEV_ENV"] = os.path.join(_scratch, "absent-jev.env")
# Langfuse's keys are read beside Jev's (`main.tracing`): no test sends a trace.
for _name in ("TYPESAFE_API_KEY", "WIKI_JEV", "WIKI_JEV_MODE", "WIKI_JEV_MODEL", "WIKI_JEV_DEGRADED",
              "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_name, None)
# An uncertain verdict in active mode would start a real host CLI (`knowledge.host_decides`):
# a test that wants the fallback turns it on and stubs the host.
os.environ["WIKI_JEV_FALLBACK"] = "off"
