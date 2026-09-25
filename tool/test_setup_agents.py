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
