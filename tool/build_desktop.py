"""Build a Windows NSIS installer from an allow-listed, self-contained payload."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import urlopen
from urllib.parse import quote, urljoin, urlsplit
import zipfile

from agent import cli_command

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.14.10"
# Python.org's release Sigstore bundle, messageSignature.messageDigest.
PYTHON_SHA256 = base64.b64decode("kJBeZKhAVX2MG8T1t02z1+6F4/+yF2EcrBx4E6zqQVk=").hex()
REPOSITORY = "littleeeeebit/wiki-agent"
DEFAULT_UPDATE_FEED = f"https://github.com/{REPOSITORY}/releases/latest/download/latest.json"
FOLDERS = ("tool", "operator", "craft", "skills", "docs", "adapters")
FILES = ("SCHEMA.md", "ENFORCEMENT.md", "MAINTENANCE.md", "README.md", "index.md", "DESIGN.md",
         "requirements-hooks.txt", "requirements-chat.txt", "eval/jev/policy.json")


def run(argv, **kwargs):
    subprocess.run(list(map(str, argv)), cwd=ROOT, check=True, **kwargs)


def https_feed(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(c.isspace() for c in value) or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use an HTTPS feed without credentials, query parameters or fragments") from None
    return value


def update_asset_url(feed: str, version: str, filename: str) -> str:
    if feed == DEFAULT_UPDATE_FEED:
        return f"https://github.com/{REPOSITORY}/releases/download/v{version}/{quote(filename)}"
    return urljoin(feed, quote(filename))


def public_key(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    decoded = base64.b64decode(value, validate=True)
    if not decoded.startswith(b"untrusted comment: minisign public key:"):
        raise ValueError("The updater public-key input must be the generated .pub file, never a private key")
    return value


def stage(destination: Path, version: str, revision: str, screen: Path | None = None) -> Path:
    """Only tracked runtime/resources, never the builder's data, keys or absolute paths."""
    if destination.exists():
        raise ValueError(f"Build directory already exists: {destination}")
    app = destination / "app"
    app.mkdir(parents=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    for name in tracked:
        path = Path(name)
        if not name or not (name in FILES or any(name.startswith(folder + "/") for folder in FOLDERS)):
            continue
        if (path.name.startswith(("test_", "conftest")) or "__pycache__" in path.parts
                or name.startswith("tool/eval/")
                or (name.startswith("adapters/") and name != "adapters/example.toml")):
            continue
        source = ROOT / path
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Not an ordinary tracked file: {name}")
        target = app / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copytree(screen or ROOT / "web/dist", app / "web/dist")
    (app / "release.json").write_text(json.dumps({"version": version, "revision": revision}) + "\n", encoding="utf-8")
    return app


def runtime(destination: Path, cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not archive.exists():
        with urlopen(f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{archive.name}", timeout=60) as response:
            archive.write_bytes(response.read(32 * 1024 * 1024))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise ValueError(f"Python release checksum failed: {archive}")
    python = destination / "python"
    python.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(python)
    # Isolated search paths also apply when a host runs a hook outside this app.
    (python / "python314._pth").write_text("python314.zip\n.\nLib/site-packages\n../app/tool\nimport site\n", encoding="utf-8")
    (python / "sitecustomize.py").write_text(
        "import sys\nsys.dont_write_bytecode = True\n"
        "for stream in (sys.stdin, sys.stdout, sys.stderr):\n"
        "    if stream is not None:\n        stream.reconfigure(encoding='utf-8', errors='replace')\n", encoding="utf-8")
    run([sys.executable, "-m", "pip", "install", "--ignore-installed", "--no-warn-conflicts", "--only-binary=:all:", "--platform", "win_amd64",
         "--implementation", "cp", "--python-version", "3.14", "--abi", "cp314", "--no-compile",
         "--target", python / "Lib/site-packages", "-r", ROOT / "requirements-chat.txt"])
    # Import smoke check uses the bundled interpreter, not the builder's environment.
    run([python / "python.exe", "-c", "import fastapi,uvicorn,wsproto,yaml,markdown_it,langfuse,ssl,sqlite3; print('Bundled runtime imports passed')"])


def smoke_backend(payload: Path) -> None:
    scratch = payload.parent / "runtime-check"
    scratch.mkdir()
    env = {k: v for k, v in os.environ.items() if not any(
        marker in k.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "LANGFUSE"))}
    env.update(WIKI_DATA_HOME=str(scratch), WIKI_USER_HOME=str(scratch), WIKI_SEARCH="off",
               JEV_ENV=str(scratch / "absent.env"), TRANSLATE_ENV=str(scratch / "absent.env"), WIKI_JEV_FALLBACK="off")
    run([payload / "python/python.exe", "-c", "import main.app; print('Bundled backend imports passed')"], env=env)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--updater-public-key", type=Path, required=True)
    parser.add_argument("--update-feed", type=https_feed, default=DEFAULT_UPDATE_FEED,
                        help="Build-time HTTPS feed for isolated acceptance; defaults to production GitHub releases")
    parser.add_argument("--allow-dirty", action="store_true", help="Development artifact only; never publish")
    args = parser.parse_args()
    if os.name != "nt" or not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Use Windows x64 and a numeric MAJOR.MINOR.PATCH version")
    if not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        parser.error("Set TAURI_SIGNING_PRIVATE_KEY to the ignored update key path (not a Windows signing certificate)")
    updater_key = public_key(args.updater_public_key)
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip()
    if dirty and not args.allow_dirty:
        parser.error("Commit source changes before building a publishable installer")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    out = ROOT / "raw/desktop/releases" / args.version
    if out.exists():
        parser.error(f"Version output already exists: {out}; use a new version")
    screen = out / "screen"
    run([*cli_command("npm"), "--prefix", "web", "run", "build", "--", "--outDir", screen, "--emptyOutDir"])
    payload = out / "payload"
    app = stage(payload, args.version, revision, screen)
    runtime(payload, ROOT / "raw/desktop/downloads")
    smoke_backend(payload)
    # Run graph generation against the staged rules and no personal repositories.
    run([payload / "python/python.exe", app / "tool/graph.py", "--json", app / "graph.json"],
        env={**os.environ, "WIKI_ROOT": str(app)})
    inventory = {p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in payload.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    (out / "payload-sha256.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    config = out / "tauri.release.json"
    config.write_text(json.dumps({"version": args.version, "bundle": {
        "active": True, "targets": ["nsis"], "createUpdaterArtifacts": True,
        "resources": {str(payload) + "/": "payload/"},
        "windows": {"nsis": {"installMode": "currentUser", "languages": ["English", "Korean"],
                              "displayLanguageSelector": True},
                    "webviewInstallMode": {"type": "embedBootstrapper"}}},
        "plugins": {"updater": {"pubkey": updater_key,
            "endpoints": [args.update_feed],
            "windows": {"installMode": "passive"}}}}, indent=2) + "\n", encoding="utf-8")
    run([*cli_command("npm"), "--prefix", "web", "exec", "--", "tauri", "build", "--config", config],
        env={**os.environ, "WIKI_DESKTOP_BUNDLE": "1", "CARGO_TARGET_DIR": str(ROOT / "raw/desktop/target")})
    bundles = ROOT / "raw/desktop/target/release/bundle/nsis"
    installer = next(bundles.glob(f"*_{args.version}_x64-setup.exe"))
    signature = installer.with_suffix(".exe.sig")
    for file in (installer, signature):
        shutil.copyfile(file, out / file.name)
    shutil.copyfile(ROOT / "raw/desktop/target/release/wiki-agent.exe", out / "wiki-agent.exe")
    (out / "latest.json").write_text(json.dumps({"version": args.version, "notes": f"wiki-agent {args.version}",
        "platforms": {"windows-x86_64": {"signature": signature.read_text(encoding="utf-8").strip(),
            "url": update_asset_url(args.update_feed, args.version, installer.name)}}}, indent=2) + "\n", encoding="utf-8")
    print(f"Installer ready: {out / installer.name}")


if __name__ == "__main__":
    main()
