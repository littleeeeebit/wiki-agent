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
from contextlib import contextmanager
from pathlib import Path

import android_owned
import verify
from common import paths

from . import github_release as gh
from . import release

LEDGER = paths.RAW / "releases"   # keyed by the canonical remote: every checkout of one repository shares it
UPLOAD_TRIES = 3
MARK = "wiki-agent-release-attempt"   # written in a draft's body, so a draft whose answer was lost is found
NOTE_LINES = 50


class Stopped(Exception):
    """An attempt that cannot go on. It ends as failed, and what it left on GitHub stays recorded."""


class Unrecorded(Exception):
    """The ledger could not be written. The attempt stays pending with its files, whatever the remote holds,
    so the next `finish` reconciles what this one did."""


class Busy(Exception):
    """Another live worker owns this attempt."""


# -- The ledger -----------------------------------------------------------------------------------

def _folder(name: str) -> Path:
    folder = release.plain(release.owned(LEDGER / name, LEDGER))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read(folder: Path) -> dict:
    file = folder / "ledger.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"attempts": []}


def _write(folder: Path, ledger: dict) -> None:
    try:
        release.replaced(folder / "ledger.json", json.dumps(ledger, ensure_ascii=False, indent=2))
    except OSError as exc:
        raise Unrecorded(f"배포 기록을 쓰지 못했다 — {exc}") from exc


def ledger(name: str) -> dict:
    with release.held(folder := _folder(name)):
        return _read(folder)


def reserve(name: str, record: dict, seen: int | None = None) -> dict:
    """This remote's one attempt, taken atomically: a pending one already there refuses the press. `seen` is
    how many attempts the ledger held when the press chose its version. Any attempt added since may have
    published, so the choice is stale and the press is refused."""

    with release.held(folder := _folder(name)):
        book = _read(folder)
        if running := next((a for a in book["attempts"] if a["state"] == "pending"), None):
            raise ValueError(f"{name} 의 배포 {running['tag']} 가 아직 진행 중이다 — 끝난 뒤 다시 누른다")
        if seen is not None and len(book["attempts"]) != seen:
            raise ValueError(f"{name} 에 버전을 고르는 사이 다른 배포가 있었다 — 카드를 새로 보고 다시 누른다")
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


@contextmanager
def working(name: str, attempt: str):
    """The live worker's hold on one attempt, from before its reservation until it ends. The OS lock goes
    with the process that holds it, so a `finish` that finds it held leaves the attempt to that worker."""

    try:
        with verify.locked(release.plain(_folder(name) / f"attempt-{attempt}.lock")):
            yield
    except verify.Pending:
        raise Busy(f"{name} 의 배포 {attempt} 를 다른 작업이 진행 중이다") from None


def _ended(record: dict, state: str, **fields) -> dict:
    """The attempt's end in the ledger, then its local folder. A folder that will not go is recorded, never
    a reason to leave the attempt pending. A ledger that cannot be written leaves it pending with its files."""

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
    book = ledger(name)
    if running := next((a for a in book["attempts"] if a["state"] == "pending"), None):
        raise ValueError(f"{name} 의 배포 {running['tag']} 가 아직 진행 중이다 — 끝난 뒤 다시 누른다")
    attempt = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    with working(name, attempt):   # from before the reservation, so no `recover` can take it from this worker
        return _pressed(repo, name, attempt, shown, bump, len(book["attempts"]), kit, halt)


def _pressed(repo: Path, name: str, attempt: str, shown: str, bump: str | None, seen: int, kit: dict,
             halt: threading.Event) -> dict:
    repo_slug = gh.slug(name)
    sha = release.pin(repo)
    cfg = release.settings(repo, sha)
    first = release.seed(repo, sha, cfg["version_source"])
    listed = gh.releases(repo_slug)
    chosen = release.accepted(shown, [_shape(r) for r in listed], gh.tags(repo_slug), bump, first)
    prior = latest(listed)
    record = reserve(name, {"id": attempt, "remote": name, "repo": str(repo), "sha": sha, "version": chosen,
                            "tag": f"v{chosen}", "state": "pending", "started": time.time(),
                            "first": prior is None, "previous": prior and prior["tag_name"]}, seen)
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
    except (Stopped, ValueError, OSError, gh.Failed) as exc:   # nothing remote was written yet
        return _ended(record, "failed", reason=str(exc) or type(exc).__name__)
    return _finish(name, attempt)


# -- The restart table ----------------------------------------------------------------------------

def _found(repo: str, record: dict) -> dict | None:
    """This attempt's own draft, by the attempt ID in its body, whatever tag it carries now: a retagged one is
    found, and then refused, rather than a second draft made beside it."""

    marker = f"<!-- {MARK} {record['id']} -->"
    return next((r for r in gh.releases(repo) if marker in (r.get("body") or "")), None)


def _complete(held: dict, manifest: list[dict]) -> bool:
    """Exactly the manifest's assets, each fully uploaded, each with the manifest's digest."""

    assets = {a["name"]: a for a in held.get("assets") or []}
    return set(assets) == {m["name"] for m in manifest} and all(
        assets[m["name"]].get("state") == "uploaded" and gh.digest(assets[m["name"]]) == m["sha256"] for m in manifest)


def _uploaded(repo: str, held: dict, item: dict, tag: str) -> bool:
    """True once this draft holds `item` whole, with the manifest's digest. False when the release went public
    meanwhile: nothing more is written to it. Every write follows a fresh read showing a draft still named
    `tag`. A `starter` asset, an upload GitHub cut off, is the only one deleted. The read after the last
    permitted upload decides, so a last upload that landed counts. When none did and the last failure was
    transient, that failure is raised, which leaves the attempt pending."""

    failed = None
    for n in range(UPLOAD_TRIES + 1):
        now = gh.release(repo, held["id"])
        if not now.get("draft"):
            return False
        if now.get("tag_name") != tag:
            raise Stopped(f"초안의 태그가 {now.get('tag_name')!r} 로 바뀌었다 — {tag} 가 아니어서 건드리지 않는다")
        asset = next((a for a in now.get("assets") or [] if a["name"] == item["name"]), None)
        if asset is not None:
            if asset.get("state") == "uploaded":
                if gh.digest(asset) == item["sha256"]:
                    return True
                raise Stopped(f"{item['name']} 이 이미 올라가 있는데 digest 가 다르다 — 다 올라간 파일은 바꾸지 않는다")
            if asset.get("state") != "starter":
                raise Stopped(f"{item['name']} 이 알 수 없는 상태 {asset.get('state')!r} 다 — 건드리지 않는다")
        if n == UPLOAD_TRIES:
            break
        _present(item)   # the local copy is needed only now, to upload
        if asset is not None:
            gh.delete_asset(repo, asset["id"])
        try:
            gh.upload(repo, held["id"], Path(item["path"]), item["name"])
        except gh.Failed as exc:
            if not exc.transient and exc.status != 422:   # 422: it is there after all; the next read decides
                raise
            failed = exc if exc.transient else None
    if failed is not None:
        raise failed
    raise Stopped(f"{item['name']} 을 {UPLOAD_TRIES} 번 올렸는데 끝나지 않았다")


def _present(item: dict) -> None:
    """The exact file the manifest names, still in the attempt's folder."""

    try:
        same = android_owned.sha256(Path(item["path"])) == item["sha256"]
    except OSError:
        same = False
    if not same:
        raise Stopped(f"{item['name']} 의 고정한 사본이 없거나 바뀌었다")


def _mismatch(repo: str, record: dict, held: dict) -> str | None:
    """Why `held` is not this attempt's release, None when its tag, its commit and its files all are."""

    if held.get("tag_name") != record["tag"]:
        return f"릴리스의 태그가 {held.get('tag_name')!r} 로 바뀌었다 — {record['tag']} 가 아니다"
    if (at := gh.tag(repo, record["tag"])) != record["sha"]:
        return f"태그 {record['tag']} 가 {str(at)[:12]} 를 가리킨다 — 고정한 {record['sha'][:12]} 가 아니다"
    if not _complete(held, record["manifest"]):
        return "릴리스의 파일이 manifest 와 다르다"
    return None


def _settled(record: dict, repo: str, held: dict) -> dict:
    """A public release, only read: published when it is exactly this attempt's, else failed and left."""

    if why := _mismatch(repo, record, held):
        return _ended(record, "failed", reason=f"공개된 릴리스가 이 시도와 다르다 — 그대로 둔다: {why}")
    assets = [{"name": a["name"], "sha256": gh.digest(a), "url": a.get("browser_download_url")} for a in held["assets"]]
    return _ended(record, "published", release_url=held.get("html_url"), assets=assets)


def finish(name: str, attempt: str) -> dict:
    """The attempt carried on from wherever it stopped, by the restart table. `Busy` when a live worker
    holds it. A transient GitHub failure, or a ledger that cannot be written, leaves it pending, so the next
    `finish` takes it up again. Anything else ends it as failed."""

    with working(name, attempt):
        return _finish(name, attempt)


def _finish(name: str, attempt: str) -> dict:
    record = next(a for a in ledger(name)["attempts"] if a["id"] == attempt)
    if record["state"] != "pending":
        return record
    repo = gh.slug(name)
    try:
        manifest = record.get("manifest")
        if not manifest:
            raise Stopped("빌드가 끝나기 전에 멈췄다 — 다시 누르면 새로 빌드한다")
        # What GitHub already holds decides first: a public release is only read, never written or retagged.
        held = gh.release(repo, record["release_id"]) if record.get("release_id") else _found(repo, record)
        if held is not None:   # known: its ID is recorded before anything else is decided about it
            record = update(name, attempt, release_id=held["id"], release_url=held.get("html_url"))
            if not held.get("draft"):
                return _settled(record, repo, held)
            if held.get("tag_name") != record["tag"]:
                raise Stopped(f"초안의 태그가 {held.get('tag_name')!r} 로 바뀌었다 — {record['tag']} 가 아니어서 건드리지 않는다")
        if (at := gh.tag(repo, record["tag"])) is None:
            gh.push_tag(repo, record["tag"], record["sha"])
            at = gh.tag(repo, record["tag"])
        if at != record["sha"]:
            raise Stopped(f"태그 {record['tag']} 가 {str(at)[:12]} 를 가리킨다 — 고정한 {record['sha'][:12]} 가 아니다")
        record = update(name, attempt, tagged=True)
        if held is None:
            try:
                held = gh.create(repo, record["tag"], record["sha"], record["title"],
                                 f"{record['notes']}\n\n<!-- {MARK} {record['id']} -->", draft=True, prerelease=False)
            except gh.Failed:
                if not (held := _found(repo, record)):   # the create's answer may be all that was lost
                    raise
        record = update(name, attempt, release_id=held["id"], release_url=held.get("html_url"))
        for item in manifest:
            if not _uploaded(repo, held, item, record["tag"]):   # someone made it public meanwhile
                return _settled(record, repo, gh.release(repo, held["id"]))
        # The last read before publishing. GitHub has no conditional publish, so a writer racing this read and
        # the PATCH can still change the draft or move the tag; `_settled` then records it failed and leaves it.
        held = gh.release(repo, held["id"])
        if not held.get("draft"):
            return _settled(record, repo, held)
        if why := _mismatch(repo, record, held):
            raise Stopped(f"공개하지 않는다: {why}")
        try:
            held = gh.publish(repo, held["id"])
        except gh.Failed as exc:
            if not exc.transient:
                raise
            held = gh.release(repo, held["id"])   # accepted but its answer lost: what GitHub holds decides
            if held.get("draft"):
                raise
        return _settled(record, repo, held)
    except gh.Failed as exc:
        if exc.transient:
            update(name, attempt, last_error=str(exc))
            raise
        return _ended(record, "failed", reason=str(exc))
    except Stopped as exc:
        return _ended(record, "failed", reason=str(exc))


def recover() -> list[dict]:
    """Every remote's pending attempt carried on, as a restarted server does. An attempt a live worker holds
    is left to it. One remote that fails never stops the sweep."""

    out = []
    for book in sorted(LEDGER.glob("*/*/*/ledger.json")) if LEDGER.is_dir() else []:
        name = book.parent.relative_to(LEDGER).as_posix()
        try:
            if running := pending(name):
                out.append(finish(name, running["id"]))
        except Busy as exc:
            out.append({"remote": name, "state": "pending", "busy": str(exc)})
        except Exception as exc:   # noqa: BLE001 — recorded per remote; the next remote is still recovered
            out.append({"remote": name, "state": "pending", "last_error": f"{type(exc).__name__}: {exc}"})
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
