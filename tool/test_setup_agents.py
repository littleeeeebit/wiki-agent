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


def _dir_link(to, at):
    """A directory link the way the installer makes one: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(to), str(at))
    else:
        os.symlink(to, at, target_is_directory=True)


def _points(link):
    return Path(os.readlink(link).removeprefix("\\\\?\\")).resolve()   # a junction names `\\?\C:\…`


def test_a_link_whose_replacement_fails_points_where_it_did(tmp_path, monkeypatch):
    """The old link went first; a new one that did not come left none, and the next plan could not list it."""

    import pytest

    import setup_agents

    old, new, link = tmp_path / "old-hub", tmp_path / "new-hub", tmp_path / "skills" / "skill"   # as `~/.claude/skills/<name>`
    old.mkdir()
    new.mkdir()
    link.parent.mkdir()
    _dir_link(old, link)
    owner, name = (__import__("_winapi"), "CreateJunction") if os.name == "nt" else (os, "symlink")
    monkeypatch.setattr(owner, name, lambda *a, **k: (_ for _ in ()).throw(OSError("access denied")))
    with pytest.raises(OSError):
        setup_agents.relink(link, new)
    assert _points(link) == old.resolve(), "실패하면 원래 링크가 그대로 돌아온다"
    # The new link fails and so does moving the old one back: it stays kept, whole, and the error names it.
    real_rename, renames = os.rename, []

    def second_fails(src, dst):
        renames.append(dst)
        if len(renames) == 2:
            raise OSError("in use")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", second_fails)
    with pytest.raises(OSError) as failed:
        setup_agents.relink(link, new)
    monkeypatch.undo()
    kept = Path(renames[0])
    assert kept.parent.parent == link.parent / ".wiki-relink" and _points(kept) == old.resolve()
    assert any(str(kept) in note for note in failed.value.__notes__), "남겨 둔 자리를 알려 준다"


def test_a_kept_link_is_listed_restored_and_only_the_confirmed_one_moves(tmp_path):
    """Recovery reads only `relink`'s own folder; a kept link goes back when this hub lacks the skill; a link
    pointing elsewhere than the confirmed plan saw is left alone."""

    import pytest

    import setup_agents

    home, old_hub = tmp_path / "home", tmp_path / "old-hub"
    (old_hub / "tool").mkdir(parents=True)
    (old_hub / "tool/hook.py").write_text("", encoding="utf-8")
    skill = old_hub / "skills/no-such-skill-here"   # a name this hub does not have
    skill.mkdir(parents=True)
    skills = home / ".claude/skills"
    skills.mkdir(parents=True)
    link = skills / skill.name
    # The owner's own link outside the skills folder, shaped like ours: never read.
    _dir_link(skill, home / ".claude" / f".{skill.name}.before-relink")
    assert setup_agents.skill_links(home) == []
    # One `relink` kept in its own folder, with nothing at its place: listed, and sent back where it pointed.
    attempt = skills / ".wiki-relink" / f"{skill.name}.cut"
    attempt.mkdir(parents=True)
    _dir_link(skill, attempt / skill.name)
    found = setup_agents.skill_links(home)
    assert [(f[0], f[1].resolve(), f[2].resolve()) for f in found] == [(link, skill.resolve(), skill.resolve())]
    setup_agents.relink(*found[0])
    assert _points(link) == skill.resolve() and not attempt.exists(), "원래 자리로 돌아오고 보관 폴더는 비운다"
    # Pointing elsewhere than the plan saw by the time the move reaches it: not the approved link, so it stays.
    with pytest.raises(ValueError, match="다른 곳"):
        setup_agents.relink(link, tmp_path, tmp_path / "somewhere-else")
    assert _points(link) == skill.resolve()


def test_relink_never_overwrites_follows_or_moves_what_it_was_not_shown(tmp_path, monkeypatch):
    """Rollback never lands on what appeared meanwhile; a link standing in for the recovery folder is not
    followed; a link retargeted while it was being moved aside is put back untouched; a missing skills folder
    comes back with the link."""

    import tempfile

    import pytest

    import setup_agents

    old, new = tmp_path / "old-hub", tmp_path / "new-hub"
    old.mkdir()
    new.mkdir()
    skills = tmp_path / "claude" / "skills"
    skills.mkdir(parents=True)
    link = skills / "skill"
    _dir_link(old, link)
    owner, name = (__import__("_winapi"), "CreateJunction") if os.name == "nt" else (os, "symlink")
    real = getattr(owner, name)

    # 1. The new link fails after a file of the owner's appeared at its place: the file stays, the old link is kept.
    def occupied(*args, **kwargs):
        link.write_text("the owner's", encoding="utf-8")
        raise OSError("access denied")

    monkeypatch.setattr(owner, name, occupied)
    with pytest.raises(OSError) as failed:
        setup_agents.relink(link, new)
    monkeypatch.undo()
    assert link.read_text(encoding="utf-8") == "the owner's", "되돌리기가 새로 생긴 파일을 덮지 않는다"
    kept = setup_agents.kept_links(skills)[link]
    assert len(kept) == 1 and _points(kept[0]) == old.resolve()
    assert any(str(kept[0]) in note for note in failed.value.__notes__)
    link.unlink()
    setup_agents.relink(link, old)   # the kept link takes its place again
    assert _points(link) == old.resolve() and not setup_agents.kept_links(skills)

    # 2. The recovery folder as a link into somebody else's folder: what is there is never read.
    elsewhere = tmp_path / "elsewhere" / "attempt"
    elsewhere.mkdir(parents=True)
    _dir_link(old, elsewhere / "other-skill")
    recovery = skills / ".wiki-relink"
    for attempt in recovery.iterdir() if recovery.exists() else []:
        attempt.rmdir()
    if recovery.exists():
        recovery.rmdir()
    _dir_link(elsewhere.parent, recovery)
    assert setup_agents.kept_links(skills) == {}, "링크로 된 보관 폴더는 따라가지 않는다"
    followed = []
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: followed.append(self) or real_is_dir(self))
    setup_agents.kept_links(skills)
    monkeypatch.undo()
    assert recovery not in followed, "링크인지 먼저 본다 — 따라가서 묻지도 않는다"
    os.rmdir(recovery) if os.name == "nt" else os.unlink(recovery)

    # 3. Retargeted between the check and the move aside: the moved link is not the approved one, so it goes back.
    real_mkdtemp = tempfile.mkdtemp

    def retarget(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
        real(str(new), str(link)) if os.name == "nt" else real(new, link, target_is_directory=True)
        return made

    monkeypatch.setattr(setup_agents.tempfile, "mkdtemp", retarget)
    with pytest.raises(ValueError, match="사이"):
        setup_agents.relink(link, tmp_path, old)
    monkeypatch.undo()
    assert _points(link) == new.resolve(), "옮기는 사이 바뀐 링크는 그대로 돌아온다"

    # 5. The recovery folder as a link: nothing is parked through it, and the link stays.
    recovery = skills / ".wiki-relink"
    for left in recovery.iterdir():
        left.rmdir()
    recovery.rmdir()
    _dir_link(elsewhere.parent, recovery)
    with pytest.raises(OSError, match="링크"):
        setup_agents.relink(link, old, new)
    assert _points(link) == new.resolve() and list(elsewhere.parent.iterdir()) == [elsewhere]
    os.rmdir(recovery) if os.name == "nt" else os.unlink(recovery)

    # 6. Replaced by a file of the owner's between the check and the move: the file goes back, not hidden.
    def to_file(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
        link.write_text("the owner's", encoding="utf-8")
        return made

    monkeypatch.setattr(setup_agents.tempfile, "mkdtemp", to_file)
    with pytest.raises(OSError):
        setup_agents.relink(link, old, new)
    monkeypatch.undo()
    assert link.read_text(encoding="utf-8") == "the owner's", "옮겨진 파일은 제자리로 돌아온다"

    # 7. Replaced by a real skill folder of the owner's: it goes back too.
    link.unlink()
    _dir_link(new, link)

    def to_folder(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
        link.mkdir()
        (link / "SKILL.md").write_text("mine", encoding="utf-8")
        return made

    monkeypatch.setattr(setup_agents.tempfile, "mkdtemp", to_folder)
    with pytest.raises(OSError):
        setup_agents.relink(link, old, new)
    monkeypatch.undo()
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "mine", "옮겨진 스킬 폴더는 제자리로 돌아온다"


def test_a_skills_folder_that_is_a_link_keeps_its_own_recovery(tmp_path, monkeypatch):
    """A skills folder linked onto another drive parks there, where `rename` reaches, and never shares what it
    kept with a sibling skills folder; a cleared attempt read meanwhile is skipped, not an error."""

    import setup_agents

    real = tmp_path / "other-drive" / "skills"
    real.mkdir(parents=True)
    claude = tmp_path / "home" / ".claude"
    claude.mkdir(parents=True)
    _dir_link(real, claude / "skills")
    attempt = real / ".wiki-relink" / "skill.cut"
    attempt.mkdir(parents=True)
    _dir_link(tmp_path, attempt / "skill")
    assert list(setup_agents.kept_links(claude / "skills")) == [claude / "skills" / "skill"]
    # Switched to a sibling skills folder: what the first one kept is not offered here.
    sibling = real.parent / "personal-skills"
    sibling.mkdir()
    os.rmdir(claude / "skills") if os.name == "nt" else os.unlink(claude / "skills")
    _dir_link(sibling, claude / "skills")
    assert setup_agents.kept_links(claude / "skills") == {} and setup_agents.skill_links(tmp_path / "home") == []
    os.rmdir(claude / "skills") if os.name == "nt" else os.unlink(claude / "skills")
    _dir_link(real, claude / "skills")

    real_stat, gone = Path.stat, []

    def cleared(self, *args, **kwargs):
        if self.name == "skill.cut" and not gone:
            gone.append(self)
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", cleared)
    assert setup_agents.kept_links(claude / "skills") == {}, "방금 치운 시도 폴더는 건너뛴다"


def test_a_kept_link_put_back_while_the_plan_reads_is_read_again(tmp_path, monkeypatch):
    """A rollback that puts the kept link back mid-read never makes the plan say nothing moves; a folder that
    keeps changing makes the plan fail, so Register checks again."""

    import pytest

    import setup_agents

    home, old_hub = tmp_path / "home", tmp_path / "old-hub"
    (old_hub / "tool").mkdir(parents=True)
    (old_hub / "tool/hook.py").write_text("", encoding="utf-8")
    skill = old_hub / "skills/no-such-skill-here"
    skill.mkdir(parents=True)
    skills = home / ".claude/skills"
    attempt = skills / ".wiki-relink" / "cut"
    attempt.mkdir(parents=True)
    kept, link = attempt / skill.name, skills / skill.name
    _dir_link(skill, kept)
    real = setup_agents._pointed

    def rolled_back(path):
        if Path(path) == kept and os.path.lexists(kept):
            os.rename(kept, link)   # a concurrent rollback puts it back between the scan and this read
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(setup_agents, "_pointed", rolled_back)
    found = setup_agents.skill_links(home)
    assert [(f[0], f[1].resolve()) for f in found] == [(link, skill.resolve())], "다시 읽어 살아 있는 링크로 본다"

    # Put back after the live scan but before the recovery scan: nothing kept is left to notice, the live view is.
    os.rename(link, kept)
    monkeypatch.setattr(setup_agents, "_pointed", real)
    real_kept = setup_agents.kept_links

    def back_first(folder):
        if os.path.lexists(kept):
            os.rename(kept, link)
        return real_kept(folder)

    monkeypatch.setattr(setup_agents, "kept_links", back_first)
    found = setup_agents.skill_links(home)
    assert [(f[0], f[1].resolve()) for f in found] == [(link, skill.resolve())], "목록이 바뀌면 다시 읽는다"
    monkeypatch.setattr(setup_agents, "kept_links", real_kept)

    # Parked between the listing and its type check, then put back: never read as "no link" and skipped.
    real_is_link = setup_agents._is_link

    def parked_then(path):
        if Path(path) == link and os.path.lexists(link) and not parked:
            parked.append(path)
            os.rename(link, kept)
            try:
                return real_is_link(path)
            finally:
                os.rename(kept, link)
        return real_is_link(path)

    parked = []
    monkeypatch.setattr(setup_agents, "_is_link", parked_then)
    found = setup_agents.skill_links(home)
    assert parked and [(f[0], f[1].resolve()) for f in found] == [(link, skill.resolve())], "사라진 항목은 다시 읽는다"
    monkeypatch.setattr(setup_agents, "_is_link", real_is_link)

    os.rename(link, kept)
    monkeypatch.setattr(setup_agents, "_pointed", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    with pytest.raises(FileNotFoundError):
        setup_agents.skill_links(home)


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
