"""The shared GitHub Releases transfer (`main.github_release`) and the fake GitHub its callers' tests use."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from main import github_release as gh


class FakeGitHub:
    """GitHub's Releases and refs REST API, held in memory for one repository, behind `github_release.api`.

    `fail[op]` is a list of statuses: each call of `op` pops one and fails with it before doing anything
    (None is a lost connection). `lose` holds ops whose effect happens and whose answer is lost. `cut` holds
    asset names whose next upload GitHub cuts off, leaving a `starter` asset and answering 502."""

    def __init__(self, repo="o/r", main="m" * 40):
        self.repo, self.main = repo, main
        self.refs: dict[str, str] = {}
        self.rels: list[dict] = []
        self.blobs: dict[int, bytes] = {}
        self.fail: dict[str, list] = {}
        self.lose: set[str] = set()
        self.cut: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.ids = iter(range(100, 10**6))

    def install(self, monkeypatch):
        monkeypatch.setattr(gh, "api", self.api)
        monkeypatch.setattr(gh, "download", self.download)
        return self

    def _maybe(self, op):
        if self.fail.get(op):
            status = self.fail[op].pop(0)
            raise gh.Failed(f"{op} 실패 (HTTP {status})" if status else f"{op} 실패 — connection reset", status)

    def _lost(self, op, answer):
        if op in self.lose:
            self.lose.discard(op)
            raise gh.Failed(f"{op} 의 답이 끊겼다", None)
        return answer

    def _rel(self, rid):
        found = next((r for r in self.rels if r["id"] == int(rid)), None)
        if found is None:
            raise gh.Failed("Not Found (HTTP 404)", 404)
        return found

    def release_named(self, tag):
        return next(r for r in self.rels if r["tag_name"] == tag)

    def add(self, tag, *, draft=False, prerelease=False, assets=(), body="", target=None):
        """A release already on GitHub, its assets given as `{name: bytes}`."""

        if not draft:
            self.refs.setdefault(tag, target or self.main)
        rel = {"id": next(self.ids), "tag_name": tag, "draft": draft, "prerelease": prerelease, "body": body,
               "name": tag, "target_commitish": target or "main", "assets": [],
               "html_url": f"https://github.com/{self.repo}/releases/tag/{tag}"}
        self.rels.append(rel)
        for name, data in dict(assets).items():
            self._store(rel, name, data, "uploaded")
        return rel

    def _store(self, rel, name, data, state):
        aid = next(self.ids)
        self.blobs[aid] = data
        asset = {"id": aid, "name": name, "state": state, "size": len(data),
                 "digest": f"sha256:{hashlib.sha256(data).hexdigest()}" if state == "uploaded" else None,
                 "browser_download_url": f"https://github.com/{self.repo}/releases/download/{rel['tag_name']}/{name}"}
        rel["assets"].append(asset)
        return asset

    def api(self, method, endpoint, body=None, upload=None, timeout=0, pages=False):
        path = endpoint.removeprefix("https://uploads.github.com/").split("?")[0]
        query = endpoint.split("?")[1] if "?" in endpoint else ""
        assert path.startswith(f"repos/{self.repo}/"), f"every call names its repository: {endpoint}"
        rest = path.removeprefix(f"repos/{self.repo}/")
        shape = re.sub(r"/\d+", "/{id}", rest)   # outside the f-string: Python 3.11 refuses a backslash there
        op = f"{method} {shape}"
        self.calls.append((method, rest))
        self._maybe(op)
        if op == "GET releases":
            return [json.loads(json.dumps(r)) for r in self.rels]
        if op == "GET releases/{id}":
            return json.loads(json.dumps(self._rel(rest.split("/")[1])))
        if op == "POST releases":
            if any(r["tag_name"] == body["tag_name"] and not r["draft"] for r in self.rels):
                raise gh.Failed("Validation Failed: already_exists (HTTP 422)", 422)
            rel = self.add(body["tag_name"], draft=body["draft"], prerelease=body["prerelease"], body=body["body"],
                           target=body.get("target_commitish"))
            return self._lost(op, json.loads(json.dumps(rel)))
        if op == "POST releases/{id}/assets":
            rel, name = self._rel(rest.split("/")[1]), re.search(r"name=([^&]+)", query).group(1)
            if any(a["name"] == name for a in rel["assets"]):
                raise gh.Failed("Validation Failed: already_exists (HTTP 422)", 422)
            data = Path(upload).read_bytes()
            if name in self.cut:
                self.cut.discard(name)
                self._store(rel, name, data[: len(data) // 2], "starter")
                raise gh.Failed("Bad Gateway (HTTP 502)", 502)
            return self._lost(op, self._store(rel, name, data, "uploaded"))
        if op == "DELETE releases/assets/{id}":
            aid = int(rest.split("/")[2])
            for rel in self.rels:
                rel["assets"] = [a for a in rel["assets"] if a["id"] != aid]
            return None
        if op == "PATCH releases/{id}":
            rel = self._rel(rest.split("/")[1])
            if rel["draft"] and not body["draft"]:
                rel["draft"] = False
                rel["published_at"] = "2026-11-18T00:00:00Z"
                self.refs.setdefault(rel["tag_name"], rel["target_commitish"])
            return self._lost(op, json.loads(json.dumps(rel)))
        if op == "GET git/matching-refs/tags":
            return [{"ref": f"refs/tags/{t}"} for t in self.refs]
        if rest.startswith("git/ref/tags/") and method == "GET":
            name = rest.removeprefix("git/ref/tags/")
            if name not in self.refs:
                raise gh.Failed("Not Found (HTTP 404)", 404)
            return {"object": {"type": "commit", "sha": self.refs[name]}}
        if op == "POST git/refs":
            name = body["ref"].removeprefix("refs/tags/")
            if name in self.refs:
                raise gh.Failed("Reference already exists (HTTP 422)", 422)
            self.refs[name] = body["sha"]
            return self._lost(op, {"ref": body["ref"]})
        raise AssertionError(f"the fake has no {op}")

    def download(self, repo, tag_name, name, into):
        assert repo == self.repo
        rel = self.release_named(tag_name)
        asset = next(a for a in rel["assets"] if a["name"] == name)
        into.mkdir(parents=True, exist_ok=True)
        (into / name).write_bytes(self.blobs[asset["id"]])
        return into / name


def test_api_names_the_repository_parses_the_answer_and_carries_the_status(monkeypatch):
    seen = []

    def sh(args, cwd, timeout=60):
        seen.append(args)
        if "--input" in args:
            body = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))
            assert body == {"draft": False}
            return subprocess.CompletedProcess(args, 0, '{"id": 7}', "")
        if "--paginate" in args:
            return subprocess.CompletedProcess(args, 0, '[[{"id": 1}], [{"id": 2}]]', "")
        return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")

    monkeypatch.setattr(gh.specs, "sh", sh)
    assert gh.publish("o/r", 7) == {"id": 7}
    assert not Path(seen[0][seen[0].index("--input") + 1]).exists()   # the staged body is gone
    assert [r["id"] for r in gh.releases("o/r")] == [1, 2]
    assert gh.tag("o/r", "v1.0.0") is None
    with pytest.raises(gh.Failed) as caught:
        gh.release("o/r", 9)
    assert caught.value.status == 404 and not caught.value.transient
    assert all(a[a.index("-X") + 2].startswith("repos/o/r/") for a in seen)
    assert all(a[2:4] == ["--hostname", "github.com"] for a in seen)   # never the CLI's default or `GH_HOST`


def test_a_download_names_its_host_with_its_repository(monkeypatch, tmp_path):
    seen = []

    def sh(args, cwd, timeout=60):
        seen.append(args)
        (tmp_path / "a.apk").write_text("x")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gh.specs, "sh", sh)
    gh.download("o/r", "v1.0.0", "a.apk", tmp_path)
    assert seen[0][seen[0].index("--repo") + 1] == "github.com/o/r"


def test_a_timed_out_call_is_a_transient_failure(monkeypatch, tmp_path):
    def slow(args, cwd, timeout=60):
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(gh.specs, "sh", slow)
    for call in (lambda: gh.release("o/r", 1), lambda: gh.download("o/r", "v1.0.0", "a.apk", tmp_path)):
        with pytest.raises(gh.Failed) as caught:
            call()
        assert caught.value.status is None and caught.value.transient


@pytest.mark.parametrize("status, said, transient", [
    (None, "connection reset", True), (502, "Bad Gateway", True), (429, "", True),
    (403, "API rate limit exceeded", True), (403, "Resource not accessible", False), (422, "", False)])
def test_which_failures_are_transient(status, said, transient):
    assert gh.Failed(said, status).transient is transient


def test_an_annotated_tag_is_read_through_to_its_commit(monkeypatch):
    answers = {"repos/o/r/git/ref/tags/v1.0.0": {"object": {"type": "tag", "sha": "t" * 40}},
               f"repos/o/r/git/tags/{'t' * 40}": {"object": {"type": "commit", "sha": "c" * 40}}}
    monkeypatch.setattr(gh, "api", lambda method, endpoint, *a, **k: answers[endpoint])
    assert gh.tag("o/r", "v1.0.0") == "c" * 40


def test_the_upload_goes_to_the_upload_host_with_the_name_quoted(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(gh, "api", lambda method, endpoint, **k: seen.append((method, endpoint, k)) or {"id": 1})
    gh.upload("o/r", 5, tmp_path / "a b.apk", "a b.apk")
    method, endpoint, k = seen[0]
    assert (method, endpoint) == ("POST", "https://uploads.github.com/repos/o/r/releases/5/assets?name=a%20b.apk")
    assert k["upload"] == tmp_path / "a b.apk"


def test_a_tag_already_there_is_left_for_the_caller_to_read_back(monkeypatch):
    fake = FakeGitHub().install(monkeypatch)
    fake.refs["v1.0.0"] = "x" * 40
    gh.push_tag("o/r", "v1.0.0", "y" * 40)
    assert fake.refs["v1.0.0"] == "x" * 40 and gh.tag("o/r", "v1.0.0") == "x" * 40
