"""release_rehearsal — the unpublished rehearsal of a release (`docs/plans/release/0-overview.md`, steps 1, 4, 5).

Two consecutive versions of a branch are built from fresh checkouts, signed with a disposable test key and
checked. Nothing is published, and the test key never signs a production artifact. The repository card's test
build runs this (`release_api`), and so does `release`'s own `rehearse` command.
"""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

from common import errorlog

from . import release


def tidy(repo: Path, at: str, left: list[str]) -> None:
    """A round's checkout removed, whatever ended the round. A removal that fails is recorded in `left`, never
    raised, so it cannot hide why the round ended."""

    try:
        if release.place(repo, at).exists():
            release.discard(repo, at)
    except (OSError, ValueError) as exc:
        errorlog.record("release-rehearsal-cleanup", exc, repo=str(repo), at=at)
        left.append(f"{release.place(repo, at)}: {exc}")


def rehearse(repo: Path, ref: str = "main", halt: threading.Event | None = None, repair=None,
             expect: str | None = None) -> dict:
    """Two consecutive versions of `ref` built, signed with one test key and checked, the second against the
    first as its previous production APK. Each build gets its own fresh checkout, removed when its round ends,
    however it ends. Nothing is published. The taken tags are the local ones, since a disposable target has no
    GitHub releases to read.

    Then the two refusals that keep an installed app updatable, on the second APK's real bytes: re-signed with
    a second test key, and checked against an application ID other than the one it was built with.

    A build that fails, or builds something wrong, goes to `repair(repo, record)` as a press's would
    (`release_publish.repaired`), so step 4's hand-off runs on this unpublished path.

    `expect`, when given, is the commit the person saw: a `ref` that has moved since is refused, not built.
    Every command before the hand-off ends within a second of `halt` (`release.stoppable`). Whatever the
    rehearsal raises carries `left`: the checkouts and test keys it could not remove."""

    halt = halt or threading.Event()
    with release.stoppable(halt):
        out, record = _rounds(repo, ref, halt, repair, expect)
    if record:
        out["repair"] = release.handed(repo, record, repair, halt)
    return out


def _rounds(repo: Path, ref: str, halt: threading.Event, repair, expect: str | None) -> tuple[dict, dict | None]:
    # A repair's pull request goes to a branch GitHub has, never a commit, a tag or a branch only this clone has.
    branch = ref.removeprefix("origin/")
    if repair is not None and release.ran(["git", "-C", str(repo), "ls-remote", "--exit-code", "--heads", "origin",
                                           f"refs/heads/{branch}"], 60).returncode:
        raise ValueError(f"`{ref}` 는 origin 의 브랜치가 아니다 — 수정 작업의 PR 이 갈 곳이 없으니 리허설할 브랜치를 골라라")
    kit = release.tools()
    attempt = f"rehearsal-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"   # two in one second
    sha = release.pin(repo, ref)
    if expect and sha != expect:
        raise ValueError(f"`{ref}` 가 카드를 읽은 뒤 {expect[:7]} 에서 {sha[:7]} 로 움직였다 — 카드를 다시 읽고 눌러라")
    cfg = release.settings(repo, sha)
    first = release.seed(repo, sha, cfg["version_source"])
    taken = release.git(repo, "tag", "--list").split()
    folder = release.owned(release.place(repo, attempt))
    folder.mkdir(parents=True)
    stores = (folder / "rehearsal.jks", folder / "other.jks")
    published, previous, rounds, refusals, needed, left, failed = [], None, [], {}, None, [], None
    try:   # from before the first key exists: every test keystore goes, whatever fails
        key = release.test_key(stores[0], kit)
        cert = release.certificate(key["keystore"], key["alias"], key["password"], kit)
        for n in (1, 2):
            ver = release.version(published, taken, None if not published else "patch", first)
            at, why = f"{attempt}/{n}", []
            try:   # one scope for the round: a partial checkout, a stop, a failed signing or inspection
                src = release.checkout(repo, sha, at)
                try:
                    apk = release.build(src, cfg["android"], ver, kit, halt)
                except release.BuildFailed as exc:   # the source's: repairable. A stop or the machine's is raised.
                    rounds.append({"version": ver, "failed": str(exc), "refused": []})
                    needed = str(exc)
                    break
                signed = release.sign(apk, folder / f"{n}-{ver}.apk", key, kit)   # outside the checkout: it stays
                seen = release.facts(signed, kit)
                why = release.refused(seen, ver, cfg["android"], cert, previous)
                rounds.append({"version": ver, "facts": seen, "refused": why})
            finally:
                tidy(repo, at, left)
            if why:
                needed = "; ".join(release.built_wrong(seen, ver, cfg["android"], previous)) or None
                break
            published.append({"tagName": f"v{ver}"})
            previous = seen
        built = len(rounds) == 2 and not any(r["refused"] or "failed" in r for r in rounds)
        if built:
            first_apk, ver = rounds[0]["facts"], rounds[1]["version"]
            second = folder / f"2-{ver}.apk"
            stranger = release.facts(release.sign(second, folder / "2-other-key.apk",
                                                  release.test_key(stores[1], kit), kit), kit)
            moved = {**cfg["android"], "application_id": cfg["android"]["application_id"] + ".moved"}
            refusals = {"certificate": release.refused(stranger, ver, cfg["android"], cert, first_apk),
                        "application_id": release.refused(release.facts(second, kit), ver, moved, cert,
                                                          {**first_apk, "package": moved["application_id"]})}
    except BaseException as exc:
        failed = exc
        exc.left = left   # the same list: the keys' removal below still reaches it
        raise
    finally:
        keys = []
        for store in stores:   # a test key never outlives the rehearsal; one locked file never spares the other
            try:
                store.unlink(missing_ok=True)
            except OSError as exc:
                errorlog.record("release-rehearsal-cleanup", exc, repo=str(repo), at=str(store))
                keys.append(f"{store}: {exc}")
        left.extend(keys)
        if keys and failed is None:   # never over the error that ended the rehearsal: that one carries them
            stuck = OSError(f"시험 키를 지우지 못했다 — 직접 지워라: {'; '.join(keys)}")
            stuck.left = left
            raise stuck
    caught = bool(refusals) and all(len([w for w in why if marker in w]) == want for why, marker, want in (
        (refusals.get("certificate", []), "서명 인증서", 2), (refusals.get("application_id", []), "앱 ID", 2)))
    out = {"sha": sha, "seed": first, "cert": cert, "folder": str(folder), "rounds": rounds,
           "refusals": refusals, "ok": built and caught, "left": left}
    record = needed and {"id": attempt, "remote": None, "tag": f"v{rounds[-1]['version']}",
                         "version": rounds[-1]["version"], "sha": sha, "base": branch, "repair": {"needed": needed}}
    return out, record or None
