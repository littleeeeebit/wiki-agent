"""`setup_agents.py --compact-window`, against a fake home only."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "setup_agents.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ) | {"WIKI_USER_HOME": str(home)})


def test_a_settings_write_cut_partway_leaves_the_owners_file_as_it_was(tmp_path, monkeypatch):
    """A full disk after the file was opened truncated the owner's settings and left invalid JSON behind."""

    import pytest

    import setup_agents

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "theme": "dark"}), encoding="utf-8")
    real_fdopen, staged = os.fdopen, []

    def half(fd, *args, **kw):   # the staging file fills halfway, then the disk is full
        out = real_fdopen(fd, *args, **kw)
        real_write = out.write
        out.write = lambda text: (real_write(text[: len(text) // 2]), staged.append(1)) and (_ for _ in ()).throw(
            OSError(28, "No space left on device"))
        return out

    monkeypatch.setattr(setup_agents.os, "fdopen", half)
    with pytest.raises(OSError):
        setup_agents.write_plan([(settings, {"model": "opus", "theme": "dark", "hooks": {}}, ["훅 추가"])])
    monkeypatch.undo()
    assert staged and json.loads(settings.read_text(encoding="utf-8")) == {"model": "opus", "theme": "dark"}
    assert list(tmp_path.iterdir()) == [settings], "끊긴 시도의 임시 파일은 남지 않는다"
    # Each attempt stages to its own file: one left by an earlier crash, or another writer's, is never installed.
    (tmp_path / "settings.json.part").write_text('{"stale": true}', encoding="utf-8")
    setup_agents.write_plan([(settings, {"model": "opus", "hooks": {}}, ["훅 추가"])])
    assert json.loads(settings.read_text(encoding="utf-8")) == {"model": "opus", "hooks": {}}


def test_a_link_whose_replacement_fails_points_where_it_did(tmp_path, monkeypatch):
    """The old link went first; a new one that did not come left none, and the next plan could not list it."""

    import pytest

    import setup_agents

    old, new, link = tmp_path / "old-hub", tmp_path / "new-hub", tmp_path / "skills" / "skill"   # as `~/.claude/skills/<name>`
    old.mkdir()
    new.mkdir()
    link.parent.mkdir()
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(old), str(link))
        real, name, owner = _winapi.CreateJunction, "CreateJunction", _winapi
    else:
        os.symlink(old, link, target_is_directory=True)
        real, name, owner = os.symlink, "symlink", os
    calls = []

    def once_fails(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("access denied")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, once_fails)
    with pytest.raises(OSError):
        setup_agents.relink(link, new)
    monkeypatch.undo()
    restored = Path(os.readlink(link).removeprefix("\\\\?\\"))   # a junction names `\\?\C:\…`
    assert restored.resolve() == old.resolve(), "실패하면 원래 링크가 그대로 돌아온다"
    # The new link fails and so does moving the old one back: it stays aside, whole, and the error names it.
    real_rename, renames = os.rename, []

    def second_fails(src, dst):
        renames.append(dst)
        if len(renames) == 2:
            raise OSError("in use")
        return real_rename(src, dst)

    monkeypatch.setattr(owner, name, lambda *a, **k: (_ for _ in ()).throw(OSError("access denied")))
    monkeypatch.setattr(os, "rename", second_fails)
    with pytest.raises(OSError) as failed:
        setup_agents.relink(link, new)
    monkeypatch.undo()
    aside = Path(renames[0])
    assert aside.parent == tmp_path and Path(os.readlink(aside).removeprefix("\\\\?\\")).resolve() == old.resolve()
    assert any(str(aside) in note for note in failed.value.__notes__), "남겨 둔 자리를 알려 준다"


def test_a_link_kept_aside_by_a_cut_move_is_listed_and_never_clobbered(tmp_path, monkeypatch):
    """A crash after the old link moved aside, before the new one stood: the next plan still points that place."""

    import pytest

    import setup_agents

    home, old_hub = tmp_path / "home", tmp_path / "old-hub"
    (old_hub / "tool").mkdir(parents=True)
    (old_hub / "tool/hook.py").write_text("", encoding="utf-8")
    (old_hub / "skills/after-merge").mkdir(parents=True)
    (home / ".claude/skills").mkdir(parents=True)
    link = home / ".claude/skills/after-merge"

    def make(at):
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(old_hub / "skills/after-merge"), str(at))
        else:
            os.symlink(old_hub / "skills/after-merge", at, target_is_directory=True)

    make(setup_agents.parked(link))   # kept aside, nothing at its own place
    found = setup_agents.skill_links(home)
    assert [(f[0], f[1].name) for f in found] == [(link, "after-merge")], "남겨 둔 링크도 계획에 든다"
    if found[0][2]:   # this hub has the skill: pointing it anew takes the kept link's place
        setup_agents.relink(link, found[0][2])
        assert os.path.lexists(link) and not os.path.lexists(setup_agents.parked(link))
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
    # A live link beside an earlier kept one: moving it aside would clobber that one, so nothing moves.
    make(link)
    make(setup_agents.parked(link))
    with pytest.raises(FileExistsError):
        setup_agents.relink(link, tmp_path)
    assert os.path.lexists(link) and os.path.lexists(setup_agents.parked(link))


def test_a_server_going_down_while_a_file_is_staged_installs_nothing(tmp_path):
    import pytest

    import setup_agents

    settings = tmp_path / "settings.json"
    settings.write_text('{"model": "opus"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="꺼지는 중"):
        setup_agents.write_plan([(settings, {"model": "opus", "hooks": {}}, ["훅 추가"])], lambda: True)
    assert json.loads(settings.read_text(encoding="utf-8")) == {"model": "opus"}
    assert list(tmp_path.iterdir()) == [settings]


def test_the_threshold_goes_into_both_hosts_and_an_existing_value_is_left_alone():
    home = Path(tempfile.mkdtemp())
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    (home / ".codex").mkdir()
    config = home / ".codex" / "config.toml"
    config.write_text('model = "x"\n\n[features]\nhooks = true\n', encoding="utf-8")

    checked = run(home, "--compact-window", "400000", "--check")
    assert checked.returncode == 1 and "맞지 않음" in checked.stdout, checked.stdout
    assert "autoCompactWindow" not in (home / ".claude" / "settings.json").read_text(encoding="utf-8")

    done = run(home, "--compact-window", "400000")
    assert done.returncode == 0, done.stdout + done.stderr
    claude = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert claude == {"model": "opus", "autoCompactWindow": 400000}
    codex = tomllib.loads(config.read_text(encoding="utf-8"))
    assert codex["model_auto_compact_token_limit"] == 400000, "not at the top level"
    assert codex["features"] == {"hooks": True} and codex["model"] == "x"

    assert run(home, "--compact-window", "400000").returncode == 0
    assert run(home, "--compact-window", "400000", "--check").returncode == 0
    assert config.read_text(encoding="utf-8").count("model_auto_compact_token_limit") == 1

    other = run(home, "--compact-window", "300000")
    assert other.returncode == 1 and "안 바꿈" in other.stdout
    assert json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))[
        "autoCompactWindow"] == 400000, "a value somebody set was overwritten"
    assert run(home, "--compact-window", "50000").returncode == 2, "under Claude's range"

    # Review round 3: a `null` is a value somebody set, not a missing key.
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"autoCompactWindow": None, "model": "opus"}), encoding="utf-8")
    kept = run(home, "--compact-window", "400000")
    assert kept.returncode == 1 and "autoCompactWindow = null" in kept.stdout, kept.stdout
    assert json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")) == {
        "autoCompactWindow": None, "model": "opus"}


def test_the_key_lands_at_the_top_level_of_any_valid_config():
    """Review round 1: a file whose last line had no newline got the key
    glued onto that line, and the command still reported success."""

    cases = {
        "no final newline": 'model = "gpt-5"',
        "empty": "",
        "a [ inside an array": 'dirs = [\n["a"],\n]\n[features]\nhooks = true',
        "a [ inside a string": 'note = """\n[not a table]\n"""\n',
    }
    for name, text in cases.items():
        home = Path(tempfile.mkdtemp())
        (home / ".codex").mkdir()
        config = home / ".codex" / "config.toml"
        config.write_text(text, encoding="utf-8")
        done = run(home, "--compact-window", "400000")
        assert done.returncode == 0, (name, done.stdout + done.stderr)
        after = tomllib.loads(config.read_text(encoding="utf-8"))
        assert after.pop("model_auto_compact_token_limit") == 400000, name
        assert after == tomllib.loads(text), (name, "the rest of the file changed")
