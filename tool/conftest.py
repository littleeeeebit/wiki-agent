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
