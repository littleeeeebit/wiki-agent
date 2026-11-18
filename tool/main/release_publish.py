"""Release step 3 (`docs/plans/release/0-overview.md`, "One attempt" and "Restart and collisions"). One attempt
publishes one pinned commit under one version as a GitHub Release. The attempt is recorded in the ledger before
any remote write, and each remote write happens only after the ledger holds what it should produce. `finish`
is the restart table: it reads where an attempt stopped and carries it on from there, or ends it as failed.
Nothing here deletes a remote tag or release. A failed attempt's tag and draft stay where they are and are
recorded as that attempt's. The one remote delete is an incomplete asset (`starter`) in this attempt's own
draft.

Steps 4 and 5 bring bounded retries, the repair turn and the button. Until then a press comes from this
module's `publish` command."""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from pathlib import Path

import android_owned
from common import paths

from . import github_release as gh
from . import release

LEDGER = paths.RAW / "releases"   # keyed by the canonical remote: every checkout of one repository shares it
UPLOAD_TRIES = 3
MARK = "wiki-agent-release-attempt"   # written in a draft's body, so a draft whose answer was lost is found
NOTE_LINES = 50


class Stopped(Exception):
    """An attempt that cannot go on. It ends as failed, and what it left on GitHub stays recorded."""


# -- The ledger -----------------------------------------------------------------------------------

def _folder(name: str) -> Path:
    folder = release.plain(release.owned(LEDGER / name, LEDGER))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read(folder: Path) -> dict:
    file = folder / "ledger.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"attempts": []}


def _write(folder: Path, ledger: dict) -> None:
    release.replaced(folder / "ledger.json", json.dumps(ledger, ensure_ascii=False, indent=2))


def ledger(name: str) -> dict:
    with release.held(folder := _folder(name)):
        return _read(folder)


def reserve(name: str, record: dict) -> dict:
    """This remote's one attempt, taken atomically: a pending one already there refuses the press."""

    with release.held(folder := _folder(name)):
        book = _read(folder)
        if running := next((a for a in book["attempts"] if a["state"] == "pending"), None):
            raise ValueError(f"{name} 의 배포 {running['tag']} 가 아직 진행 중이다 — 끝난 뒤 다시 누른다")
        book["attempts"].append(record)
        _write(folder, book)
    return record


def update(name: str, attempt: str, **fields) -> dict:
    with release.held(folder := _folder(name)):
        book = _read(folder)
        record = next(a for a in book["attempts"] if a["id"] == attempt)
        record.update(fields)
        _write(folder, book)
    return dict(record)


def pending(name: str) -> dict | None:
    return next((a for a in ledger(name)["attempts"] if a["state"] == "pending"), None)


def _ended(record: dict, state: str, **fields) -> dict:
    """The attempt's end in the ledger, then its local folder. A folder that will not go is recorded, never
    a reason to leave the attempt pending."""

    done = update(record["remote"], record["id"], state=state, ended=time.time(), **fields)
    try:
        release.discard(Path(record["repo"]), record["id"])
    except (OSError, ValueError) as exc:
        done = update(record["remote"], record["id"], left=f"{release.place(Path(record['repo']), record['id'])}: {exc}")
    return done


# -- What the press publishes ---------------------------------------------------------------------

def _shape(r: dict) -> dict:   # the selector's view of a release, as `release.version` reads it
    return {"tagName": r.get("tag_name") or "", "isDraft": r.get("draft"), "isPrerelease": r.get("prerelease")}


def latest(releases: list[dict]) -> dict | None:
    """The last eligible production release: published, not a prerelease, tagged `v<major>.<minor>.<patch>`."""

    eligible = [(v, r) for r in releases if not r.get("draft") and not r.get("prerelease")
                and (v := release.parsed(r.get("tag_name") or ""))]
    return max(eligible, key=lambda pair: pair[0])[1] if eligible else None


def notes(repo: Path, sha: str, since: str | None, first: bool) -> str:
    """The first release's note, then the pull requests merged up to the pinned commit: one line each, read
    from `main`'s first-parent history, which holds one squashed commit per pull request."""

    span = [f"{since}..{sha}"] if since else [sha]
    try:
        log = release.git(repo, "log", "--first-parent", f"--max-count={NOTE_LINES}", "--format=%s", *span)
        titles = [f"- {t}" for t in log.splitlines() if t.strip()] or ["이전 릴리스 뒤로 병합된 변경이 없다."]
    except ValueError:
        titles = ["변경 목록을 만들지 못했다."]
    return "\n".join(([release.FIRST_RELEASE_NOTE, ""] if first else []) + titles)


def previous_facts(repo: str, prior: dict | None, abi: str, into: Path, kit: dict) -> dict | None:
    """The previous production APK's facts, read from its real bytes on GitHub. None before the first."""

    if prior is None:
        return None
    apks = [a for a in prior.get("assets") or [] if a["name"].endswith(f"-{abi}.apk")]
    if len(apks) != 1:
        raise Stopped(f"{prior['tag_name']} 에 {abi} APK 가 {len(apks)} 개다 — 이전 정식 APK 를 하나로 정할 수 없다")
    file = gh.download(repo, prior["tag_name"], apks[0]["name"], into)
    if gh.digest(apks[0]) not in (None, android_owned.sha256(file)):
        raise Stopped(f"{prior['tag_name']} 의 APK 가 GitHub 이 기록한 digest 와 다르다")
    return release.facts(file, kit)


def publish(repo: Path, shown: str, bump: str | None, halt: threading.Event | None = None) -> dict:
    """One press: pin, check the shown version, reserve, build, sign, verify, record the manifest, then
    `finish`. A refusal before the reservation writes nothing."""

    halt, kit = halt or threading.Event(), release.tools()
    name = release.remote(repo)
    repo_slug = gh.slug(name)
    if running := pending(name):
        raise ValueError(f"{name} 의 배포 {running['tag']} 가 아직 진행 중이다 — 끝난 뒤 다시 누른다")
    sha = release.pin(repo)
    cfg = release.settings(repo, sha)
    first = release.seed(repo, sha, cfg["version_source"])
    listed = gh.releases(repo_slug)
    chosen = release.accepted(shown, [_shape(r) for r in listed], gh.tags(repo_slug), bump, first)
    prior = latest(listed)
    attempt = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    record = reserve(name, {"id": attempt, "remote": name, "repo": str(repo), "sha": sha, "version": chosen,
                            "tag": f"v{chosen}", "state": "pending", "started": time.time(),
                            "first": prior is None, "previous": prior and prior["tag_name"]})
    try:
        work = release.owned(release.place(repo, attempt))
        work.mkdir(parents=True)
        key = release.release_key(repo, kit, create=prior is None)
        before = previous_facts(repo_slug, prior, cfg["android"]["abi"], work / "previous", kit)
        src = release.checkout(repo, sha, f"{attempt}/src")
        asset = f"{repo_slug.split('/')[1]}-{chosen}-{cfg['android']['abi']}.apk"
        signed = release.sign(release.build(src, cfg["android"], chosen, kit, halt), work / asset, key, kit)
        seen = release.facts(signed, kit)
        if why := release.refused(seen, chosen, cfg["android"], key["cert"], before):
            raise Stopped("; ".join(why))
        release.discard(repo, f"{attempt}/src")   # large; the signed APK beside it is what goes up
        since = gh.tag(repo_slug, prior["tag_name"]) if prior else None
        record = update(name, attempt, facts=seen, title=f"v{chosen}", notes=notes(repo, sha, since, prior is None),
                        manifest=[{"name": asset, "size": signed.stat().st_size,
                                   "sha256": android_owned.sha256(signed), "path": str(signed)}])
    except (Stopped, ValueError, OSError, gh.Failed) as exc:
        return _ended(record, "failed", reason=str(exc) or type(exc).__name__)
    return finish(name, attempt)


# -- The restart table ----------------------------------------------------------------------------

def _found(repo: str, record: dict) -> dict | None:
    """This attempt's own draft, by its tag and the attempt ID in its body."""

    marker = f"<!-- {MARK} {record['id']} -->"
    return next((r for r in gh.releases(repo) if r.get("tag_name") == record["tag"] and marker in (r.get("body") or "")), None)


def _complete(held: dict, manifest: list[dict]) -> bool:
    """Exactly the manifest's assets, each fully uploaded, each with the manifest's digest."""

    assets = {a["name"]: a for a in held.get("assets") or []}
    return set(assets) == {m["name"] for m in manifest} and all(
        assets[m["name"]].get("state") == "uploaded" and gh.digest(assets[m["name"]]) == m["sha256"] for m in manifest)


def _uploaded(repo: str, held: dict, item: dict) -> None:
    for _ in range(UPLOAD_TRIES):
        now = {a["name"]: a for a in gh.release(repo, held["id"]).get("assets") or []}
        if (asset := now.get(item["name"])) is not None:
            if asset.get("state") != "uploaded":   # cut off: this draft's own incomplete upload
                gh.delete_asset(repo, asset["id"])
            elif gh.digest(asset) == item["sha256"]:
                return
            else:
                raise Stopped(f"{item['name']} 이 이미 올라가 있는데 digest 가 다르다 — 다 올라간 파일은 바꾸지 않는다")
        try:
            gh.upload(repo, held["id"], Path(item["path"]), item["name"])
        except gh.Failed as exc:
            if not exc.transient and exc.status != 422:   # 422: it is there after all; the next read decides
                raise
    raise Stopped(f"{item['name']} 을 {UPLOAD_TRIES} 번 올렸는데 끝나지 않았다")


def finish(name: str, attempt: str) -> dict:
    """The attempt carried on from wherever it stopped, by the restart table. A transient GitHub failure
    leaves it pending, so the next `finish` takes it up again. Anything else ends it as failed."""

    record = next(a for a in ledger(name)["attempts"] if a["id"] == attempt)
    repo = gh.slug(name)
    try:
        if record["state"] != "pending":
            return record
        manifest = record.get("manifest")
        if not manifest:
            raise Stopped("빌드가 끝나기 전에 멈췄다 — 다시 누르면 새로 빌드한다")
        for item in manifest:
            if not Path(item["path"]).is_file() or android_owned.sha256(Path(item["path"])) != item["sha256"]:
                raise Stopped(f"{item['name']} 의 고정한 사본이 없거나 바뀌었다")
        if (at := gh.tag(repo, record["tag"])) is None:
            gh.push_tag(repo, record["tag"], record["sha"])
            at = gh.tag(repo, record["tag"])
        if at != record["sha"]:
            raise Stopped(f"태그 {record['tag']} 가 {str(at)[:12]} 를 가리킨다 — 고정한 {record['sha'][:12]} 가 아니다")
        record = update(name, attempt, tagged=True)
        if record.get("release_id"):
            held = gh.release(repo, record["release_id"])
        elif not (held := _found(repo, record)):
            try:
                held = gh.create(repo, record["tag"], record["sha"], record["title"],
                                 f"{record['notes']}\n\n<!-- {MARK} {record['id']} -->", draft=True, prerelease=False)
            except gh.Failed:
                if not (held := _found(repo, record)):   # the create's answer may be all that was lost
                    raise
        record = update(name, attempt, release_id=held["id"], release_url=held.get("html_url"))
        if held.get("draft"):
            for item in manifest:
                _uploaded(repo, held, item)
            held = gh.release(repo, held["id"])
            if not _complete(held, manifest):
                raise Stopped("초안의 파일이 manifest 와 다르다 — 공개하지 않는다")
            try:
                held = gh.publish(repo, held["id"])
            except gh.Failed as exc:
                if not exc.transient:
                    raise
                held = gh.release(repo, held["id"])   # accepted but its answer lost: what GitHub holds decides
                if held.get("draft"):
                    raise
        # Public now, by this call or one whose answer or ledger write was lost: read-only from here.
        if gh.tag(repo, record["tag"]) != record["sha"] or not _complete(held, manifest):
            raise Stopped("공개된 릴리스의 태그나 파일이 이 시도와 다르다 — 그대로 두고 실패로 기록한다")
        assets = [{"name": a["name"], "sha256": gh.digest(a), "url": a.get("browser_download_url")}
                  for a in held["assets"]]
        return _ended(record, "published", release_url=held.get("html_url"), assets=assets)
    except gh.Failed as exc:
        if exc.transient:
            update(name, attempt, last_error=str(exc))
            raise
        return _ended(record, "failed", reason=str(exc))
    except (Stopped, OSError) as exc:
        return _ended(record, "failed", reason=str(exc) or type(exc).__name__)


def recover() -> list[dict]:
    """Every remote's pending attempt carried on, as a restarted server does."""

    out = []
    for book in sorted(LEDGER.glob("*/*/*/ledger.json")) if LEDGER.is_dir() else []:
        name = book.parent.relative_to(LEDGER).as_posix()
        if running := pending(name):
            try:
                out.append(finish(name, running["id"]))
            except gh.Failed as exc:
                out.append({**running, "last_error": str(exc)})
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["recover"]:
        print(json.dumps(recover(), ensure_ascii=False, indent=2, default=str))
        return 0
    if len(argv) < 3 or argv[0] != "publish":
        print("usage: publish <repo> <shown version> [patch|minor|major] | recover", file=sys.stderr)
        return 2
    done = publish(Path(argv[1]).resolve(), argv[2], argv[3] if len(argv) > 3 else None)
    print(json.dumps({k: v for k, v in done.items() if k != "facts"}, ensure_ascii=False, indent=2, default=str))
    return 0 if done["state"] == "published" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
