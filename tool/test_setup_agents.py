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
