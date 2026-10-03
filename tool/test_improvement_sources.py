"""Private candidate repositories stay out of normal document discovery."""

import subprocess

import pytest

import corpus
from search import sources


@pytest.mark.parametrize("git_available", [True, False])
def test_private_project_trials_never_enter_hub_retrieval(tmp_path, monkeypatch, git_available):
    hub = tmp_path / "hub"
    hub.mkdir()
    own = hub / "README.md"
    own.write_text("Hub documentation\n", encoding="utf-8")
    private = hub / "raw/improvement/project/repo-key/run/checkouts/candidate/README.md"
    private.parent.mkdir(parents=True)
    private.write_text("Another project's private evidence\n", encoding="utf-8")

    def listing(*args, **kwargs):
        if not git_available:
            raise subprocess.SubprocessError("Synthetic Git outage")
        return subprocess.CompletedProcess(args, 0, b"README.md\0raw/improvement/project/repo-key/run/checkouts/candidate/README.md\0")

    monkeypatch.setattr(sources.subprocess, "run", listing)
    assert sources.listing(hub, hub) == [own]
    # Explicit evaluation inside that candidate keeps its own documents available.
    if not git_available:
        assert sources.listing(hub, private.parent) == [private]
    assert corpus.walk(hub, [".", "raw"]) == [own]
    assert corpus.walk(hub, [str(private.relative_to(hub))]) == []
