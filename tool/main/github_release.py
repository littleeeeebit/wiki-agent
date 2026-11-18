"""The one GitHub Releases transfer both publishers share (`docs/plans/release/0-overview.md`, "Where it lives"):
S8's debug prerelease and the production release. Every call names its repository as `owner/name`, and nothing
reads a working directory's remote. Policy stays with each caller: this replaces no asset and deletes nothing
on its own."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from common import paths

from . import specs

SECONDS, UPLOAD_SECONDS = 120, 1800


class Failed(ValueError):
    """A GitHub call that did not succeed. `status` is the HTTP status, or None when no answer came."""

    def __init__(self, what: str, status: int | None):
        super().__init__(what)
        self.status = status

    @property
    def transient(self) -> bool:   # no answer, rate limited, or GitHub's own fault: the same call may succeed
        limited = self.status == 403 and "rate limit" in str(self).lower()
        return self.status is None or self.status == 429 or self.status >= 500 or limited


def slug(remote: str) -> str:
    """`owner/name` for a canonical remote, `github.com/<owner>/<name>`."""

    return remote.removeprefix("github.com/")


def api(method: str, endpoint: str, body: dict | None = None, upload: Path | None = None,
        timeout: float = SECONDS, pages: bool = False):
    """One `gh api` call: the parsed answer, or `Failed`. A JSON body and an uploaded file both go through
    `--input`, because the shared runner gives the child no stdin."""

    args = ["gh", "api", "-X", method, endpoint, "-H", "Accept: application/vnd.github+json"]
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
        done = specs.sh(args, paths.RESOURCES, timeout)
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
    done = specs.sh(["gh", "release", "download", tag_name, "--repo", repo, "--pattern", name, "--dir", str(into),
                     "--clobber"], paths.RESOURCES, UPLOAD_SECONDS)
    if done.returncode or not (into / name).is_file():
        said = (done.stderr or done.stdout or "").strip()
        found = re.search(r"HTTP (\d{3})", said)
        raise Failed(f"{tag_name} 의 {name} 을 받지 못했다 — {said[-300:]}", int(found.group(1)) if found else None)
    return into / name
