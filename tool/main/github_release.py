"""The one GitHub Releases transfer both publishers share (`docs/plans/release/0-overview.md`, "Where it lives"):
S8's debug prerelease and the production release. Every call names its repository as `owner/name`, and nothing
reads a working directory's remote. Policy stays with each caller: this replaces no asset and deletes nothing
on its own."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from common import paths

from . import specs

SECONDS, UPLOAD_SECONDS = 120, 1800
HOST = "github.com"   # every remote `release.remote` accepts is on github.com


class Failed(ValueError):
    """A GitHub call that did not succeed. `status` is the HTTP status, or None when no answer came."""

    def __init__(self, what: str, status: int | None):
        super().__init__(what)
        self.status = status

    @property
    def transient(self) -> bool:   # no answer, rate limited, or GitHub's own fault: the same call may succeed
        limited = self.status == 403 and "rate limit" in str(self).lower()
        return self.status is None or self.status == 429 or self.status >= 500 or limited


class Halted(Exception):
    """A write a stop reached before it was launched: nothing was sent."""


_launch = threading.Lock()   # a write's launch and a stop's signal are ordered: never both at once
_stopping = threading.local()   # the halt of the press or sweep running on this thread (`stopping`)


@contextmanager
def stopping(halt: threading.Event | None):
    """Every write made on this thread inside the block is refused (`Halted`) once `halt` is set by `stop`."""

    prior = getattr(_stopping, "halt", None)
    _stopping.halt = halt
    try:
        yield
    finally:
        _stopping.halt = prior


def stop(halt: threading.Event) -> None:
    """Sets `halt` so that no write launches after this returns: a write already past its check was launched
    before the stop, and what it did is read back by whoever carries the attempt on."""

    with _launch:
        halt.set()


def slug(remote: str) -> str:
    """`owner/name` for a canonical remote, `github.com/<owner>/<name>`."""

    return remote.removeprefix("github.com/")


def api(method: str, endpoint: str, body: dict | None = None, upload: Path | None = None,
        timeout: float = SECONDS, pages: bool = False):
    """One `gh api` call: the parsed answer, or `Failed`. A JSON body and an uploaded file both go through
    `--input`, because the shared runner gives the child no stdin."""

    # `--hostname`: the canonical remote is github.com, whatever host `GH_HOST` or the CLI's default names.
    args = ["gh", "api", "--hostname", HOST, "-X", method, endpoint, "-H", "Accept: application/vnd.github+json"]
    if pages:
        args += ["--paginate", "--slurp"]
    staged = None
    try:
        if body is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as staged:
                staged.write(json.dumps(body))
            args += ["--input", staged.name]
        elif upload is not None:
            args += ["--input", str(upload), "-H", "Content-Type: application/octet-stream"]
        if method != "GET":   # the last moment before a write leaves: checked under the lock `stop` sets under
            with _launch:
                halt = getattr(_stopping, "halt", None)
                if halt is not None and halt.is_set():
                    raise Halted(f"멈춤 — GitHub {method} 을 보내기 전에 멈췄다. 이어 가면 남은 것부터 올린다")
        done = specs.sh(args, paths.RESOURCES, timeout)
    except subprocess.TimeoutExpired as exc:   # no answer in time: it may still have landed, so read back
        raise Failed(f"GitHub {method} {endpoint.split('?')[0]} 이 {exc.timeout}초 안에 답하지 않았다", None) from exc
    finally:
        if staged is not None:
            os.unlink(staged.name)
    if done.returncode:
        said = (done.stderr or done.stdout or "").strip()
        found = re.search(r"HTTP (\d{3})", said)
        raise Failed(f"GitHub {method} {endpoint.split('?')[0]} 실패 — {said[-300:]}",
                     int(found.group(1)) if found else None)
    text = (done.stdout or "").strip()
    answer = json.loads(text) if text else None
    return [item for page in answer for item in page] if pages else answer


def releases(repo: str) -> list[dict]:
    """Every release, drafts included: drafts are visible only to someone who can push, as the server can."""

    return api("GET", f"repos/{repo}/releases?per_page=100", pages=True)


def release(repo: str, release_id: int) -> dict:
    return api("GET", f"repos/{repo}/releases/{release_id}")


def create(repo: str, tag: str, target: str | None, title: str, notes: str, draft: bool, prerelease: bool) -> dict:
    """A new release. `target` None leaves GitHub's default branch, which S8 has always used. GitHub ignores
    the target when the tag already exists, so a caller that needs the commit reads the tag back."""

    body = {"tag_name": tag, "name": title, "body": notes, "draft": draft, "prerelease": prerelease}
    return api("POST", f"repos/{repo}/releases", {**body, **({"target_commitish": target} if target else {})})


def upload(repo: str, release_id: int, file: Path, name: str) -> dict:
    """`file` as the asset `name`. An asset of that name already there makes GitHub refuse it (422)."""

    return api("POST", f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={quote(name)}",
               upload=file, timeout=UPLOAD_SECONDS)


def delete_asset(repo: str, asset_id: int) -> None:
    api("DELETE", f"repos/{repo}/releases/assets/{asset_id}")


def publish(repo: str, release_id: int) -> dict:
    return api("PATCH", f"repos/{repo}/releases/{release_id}", {"draft": False})


def tags(repo: str) -> list[str]:
    """Every tag name on the remote."""

    return [r["ref"].removeprefix("refs/tags/") for r in api("GET", f"repos/{repo}/git/matching-refs/tags", pages=True)]


def tag(repo: str, name: str) -> str | None:
    """The commit the tag points at, through an annotated tag object, or None when there is no such tag."""

    try:
        found = api("GET", f"repos/{repo}/git/ref/tags/{quote(name)}")["object"]
    except Failed as exc:
        if exc.status == 404:
            return None
        raise
    while found["type"] == "tag":
        found = api("GET", f"repos/{repo}/git/tags/{found['sha']}")["object"]
    return found["sha"]


def push_tag(repo: str, name: str, sha: str) -> None:
    """A lightweight tag at `sha`. One already there is left as it is, and the caller reads it back."""

    try:
        api("POST", f"repos/{repo}/git/refs", {"ref": f"refs/tags/{name}", "sha": sha})
    except Failed as exc:
        if exc.status != 422:
            raise


def digest(asset: dict) -> str | None:
    """The asset's SHA-256 as GitHub computed it from the bytes it holds."""

    found = asset.get("digest") or ""
    return found.removeprefix("sha256:") if found.startswith("sha256:") else None


def download(repo: str, tag_name: str, name: str, into: Path) -> Path:
    """One asset of a published release, into the folder `into`."""

    into.mkdir(parents=True, exist_ok=True)
    try:
        done = specs.sh(["gh", "release", "download", tag_name, "--repo", f"{HOST}/{repo}", "--pattern", name,
                         "--dir", str(into), "--clobber"], paths.RESOURCES, UPLOAD_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise Failed(f"{tag_name} 의 {name} 을 {exc.timeout}초 안에 받지 못했다", None) from exc
    if done.returncode or not (into / name).is_file():
        said = (done.stderr or done.stdout or "").strip()
        found = re.search(r"HTTP (\d{3})", said)
        raise Failed(f"{tag_name} 의 {name} 을 받지 못했다 — {said[-300:]}", int(found.group(1)) if found else None)
    return into / name
