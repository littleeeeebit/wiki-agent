"""Every test runs against an empty home, never the machine's user-level hooks.

Set in the environment rather than patched on the module, so the installers
the tests start as subprocesses see the same empty home.
"""

import os
import tempfile

os.environ["WIKI_USER_HOME"] = tempfile.mkdtemp(prefix="wiki-home-")

# `search.ask` reaches the daemon only when this is unset. A test that spawned
# one would start a real daemon and download its model.
os.environ["WIKI_SEARCH"] = "off"

# No key and no seeded cache: opening a pull request translates it, and with
# the checkout's `.env` every such test spent a live Gemini request.
_scratch = tempfile.mkdtemp(prefix="wiki-translate-")
os.environ["TRANSLATE_ENV"] = os.path.join(_scratch, "absent.env")
os.environ["TRANSLATE_CACHE"] = os.path.join(_scratch, "cache.sqlite3")

# The same for Jev: the hub's `.env` holds a live TypeSafe key, and a machine
# may export one too. No test reads either; a test that wants a key writes
# its own file and points `JEV_ENV` at it.
os.environ["JEV_ENV"] = os.path.join(_scratch, "absent-jev.env")
for _name in ("TYPESAFE_API_KEY", "WIKI_JEV", "WIKI_JEV_MODE", "WIKI_JEV_MODEL"):
    os.environ.pop(_name, None)
