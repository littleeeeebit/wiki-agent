"""The oneshot base commit's files, and the Git reads that check it holds exactly them.

Used by the bootstrap's sub-steps in `oneshot`. Pure apart from `git`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import connect, specs
from .oneshot_profile import gate_cmd


def planned(run: dict) -> dict[str, str]:
    """Every file the base commit holds, by path, as the run writes it."""

    p, intent, name = run["profile"], run["intent"], run["identity"]["name"]
    ignore = [".venv/", "__pycache__/", "*.pyc", ".pytest_cache/", ".env", "node_modules/", "dist/", "artifacts/"]
    setup = {"uv": "uv sync",
             "venv": f"python -m venv .venv\n.venv/{'Scripts' if p['os_now'] == 'windows' else 'bin'}/python -m pip install -e .",
             "conda": f"conda create -n {p.get('conda_env')} python=3.12\nconda activate {p.get('conda_env')}"}[p["python_env"]]
    readme = (f"# {name}\n\n{intent['restated']}\n\n> {run['sentence']}\n\n"
              f"## Setup\n\n```\n{setup}\n```\n")
    files = {".gitignore": "\n".join(ignore) + "\n", "README.md": readme,
             ".wiki/adapter.toml": connect.adapter_text({"gate_cmd": gate_cmd(p), "review_dir": "artifacts/review",
                                                         "scratch_dirs": "artifacts/", "live_cmd": "", "server_stop": ""})}
    if p["os_later"] == "other":
        files[".gitattributes"] = "* text=auto eol=lf\n"
    if p["visibility"] == "public" and run.get("license_text"):
        files["LICENSE"] = run["license_text"]
    return files


def git(path: Path, *args: str, timeout: float = 60):
    return specs.sh(["git", *args], path, timeout)


def tracked(path: Path) -> set[str]:
    done = git(path, "ls-tree", "-r", "--name-only", "main")
    return set(done.stdout.splitlines()) if not done.returncode else set()


def blob(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def as_planned(path: Path, files: dict[str, str]) -> bool:
    """`main` is one commit holding exactly `files`, each byte for byte as planned."""

    if tracked(path) != set(files) or git(path, "rev-list", "--count", "main").stdout.strip() != "1":
        return False
    return all(git(path, "rev-parse", f"main:{rel}").stdout.strip() == blob(text) for rel, text in files.items())
