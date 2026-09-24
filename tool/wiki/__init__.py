"""wiki — match rules to an utterance, answer from the wiki.

Nothing here translates. A question goes in as the person typed it and the
matched pages come back as written; weaving in `translate` is the job of
whoever calls this — the hook entry point (`tool/inject.py`) or the server.

`__all__` is the whole contract. `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else. Two kinds of caller use it: the mains,
which ask what an utterance matches (`pages`, `match_pages`, `render_parts`),
and the wiki's own tools, which read the page format (`front_matter`,
`hub_pages`, `project_pages`, ...).
"""

from .match import (
    INJECTABLE, REPO_BUDGET, RULE_BUDGET, SLOT, adapter_path, budget, label,
    match_pages, pages, render_parts, rule_index, slots_for, source_map,
)
from .wikilib import (
    SCOPES, WIKI, front_matter, hub_pages, links_of, metadata_errors,
    project_pages, resolve,
)

__all__ = (
    # asking what an utterance matches
    "pages", "match_pages", "render_parts", "rule_index", "source_map", "label",
    "budget", "RULE_BUDGET", "REPO_BUDGET",
    # reading the page format
    "WIKI", "SCOPES", "front_matter", "metadata_errors", "links_of", "resolve",
    "hub_pages", "project_pages", "INJECTABLE", "SLOT", "adapter_path", "slots_for",
)
