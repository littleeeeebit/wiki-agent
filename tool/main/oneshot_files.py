"""The oneshot base commit's files, and the Git reads that check it holds exactly them.

Used by the bootstrap's sub-steps in `oneshot`. Pure apart from `git`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import connect, specs

# Nothing stack-shaped exists before S2, so the list covers the common stacks' build and cache output.
IGNORE = [".env", "artifacts/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".pytest_cache/", "node_modules/",
          "dist/", "build/", "target/", "bin/", "obj/", "coverage/", ".next/", ".nuxt/", ".svelte-kit/",
          ".gradle/", "vendor/", ".DS_Store"]


def planned(run: dict) -> dict[str, str]:
    """Every file the base commit holds, by path, as the run writes it. The adapter's gate is empty until
    the tests turn declares the project's own test command (S4)."""

    p, intent, name = run["profile"], run["intent"], run["identity"]["name"]
    readme = f"# {name}\n\n{intent['restated']}\n\n> {run['sentence']}\n"
    files = {".gitignore": "\n".join(IGNORE) + "\n", "README.md": readme,
             ".wiki/adapter.toml": connect.adapter_text({"gate_cmd": "", "review_dir": "artifacts/review",
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
