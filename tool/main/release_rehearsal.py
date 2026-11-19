"""release_rehearsal — the unpublished rehearsal of a release (`docs/plans/release/0-overview.md`, steps 1, 4-6).

Two consecutive versions of a branch are built from fresh checkouts, each declared kind in each, the APK signed
with a disposable test key, and checked. Nothing is published, and the test key never signs a production artifact. The repository card's test
build runs this (`release_api`), and so does `release`'s own `rehearse` command.
"""

from __future__ import annotations

import secrets
import shutil
import threading
import time
from pathlib import Path

from common import errorlog

from . import release, release_desktop


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

    Then the refusals that keep an installed app updatable, on the second round's real bytes: the APK re-signed
    with a second test key, and checked against an application ID other than the one it was built with; the
    installer checked against a product name and a publisher other than the ones it was built with.

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
    attempt = f"rehearsal-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"   # two in one second
    sha = release.pin(repo, ref)
    if expect and sha != expect:
        raise ValueError(f"`{ref}` 가 카드를 읽은 뒤 {expect[:7]} 에서 {sha[:7]} 로 움직였다 — 카드를 다시 읽고 눌러라")
    cfg = release.settings(repo, sha)
    kit, android = release.tools(cfg), cfg.get("android")
    first = release.seed(repo, sha, cfg["version_source"])
    taken = release.git(repo, "tag", "--list").split()
    folder = release.owned(release.place(repo, attempt))
    folder.mkdir(parents=True)
    stores = (folder / "rehearsal.jks", folder / "other.jks")
    published, previous, rounds, refusals, needed, left, cert = [], {}, [], {}, None, [], None
    try:   # from before the first key exists: every test keystore goes, whatever fails
        if android:
            key = release.test_key(stores[0], kit)
            cert = release.certificate(key["keystore"], key["alias"], key["password"], kit)
        for n in (1, 2):
            ver = release.version(published, taken, None if not published else "patch", first)
            at, why, wrong, seen, failed = f"{attempt}/{n}", [], [], {}, None
            try:   # one scope for the round: a partial checkout, a stop, a failed signing or inspection
                src = release.checkout(repo, sha, at)
                for kind in [k for k in release.KINDS if k in cfg]:   # every declared kind, from the one checkout
                    try:
                        built = release.build(src, cfg[kind], ver, kit, halt,
                                              **({"read": release_desktop.readable} if kind == "desktop" else {}))
                    except release.BuildFailed as exc:   # the source's: repairable. A stop or the machine's is raised.
                        failed = str(exc)
                        break
                    if kind == "android":   # outside the checkout: it stays
                        seen[kind] = release.facts(release.sign(built, folder / f"{n}-{ver}.apk", key, kit), kit)
                        why += release.refused(seen[kind], ver, android, cert, previous.get(kind))
                        wrong += release.built_wrong(seen[kind], ver, android, previous.get(kind))
                    else:
                        shutil.copyfile(built, folder / f"{n}-{ver}{release_desktop.SUFFIX}")
                        seen[kind] = release_desktop.facts(folder / f"{n}-{ver}{release_desktop.SUFFIX}",
                                                           release.output(src, cfg[kind], ver, "exe"))
                        got = release_desktop.built_wrong(seen[kind], ver, cfg[kind], previous.get(kind))
                        why += got   # each is the build's, so each is also what the repair needs
                        wrong += got
                    if why:
                        break
                rounds.append({"version": ver, "failed": failed, "refused": []} if failed else
                              {"version": ver, "facts": seen, "refused": why})
            finally:
                tidy(repo, at, left)
            if failed or why:
                needed = failed or "; ".join(wrong) or None
                break
            published.append({"tagName": f"v{ver}"})
            previous = seen
        built = len(rounds) == 2 and not any(r["refused"] or "failed" in r for r in rounds)
        if built:
            ver, first_seen, second_seen = rounds[1]["version"], rounds[0]["facts"], rounds[1]["facts"]
            if android:
                second = folder / f"2-{ver}.apk"
                stranger = release.facts(release.sign(second, folder / "2-other-key.apk",
                                                      release.test_key(stores[1], kit), kit), kit)
                moved = {**android, "application_id": android["application_id"] + ".moved"}
                refusals["certificate"] = release.refused(stranger, ver, android, cert, first_seen["android"])
                refusals["application_id"] = release.refused(
                    second_seen["android"], ver, moved, cert, {**first_seen["android"], "package": moved["application_id"]})
            if "desktop" in cfg:   # the second installer's real bytes against a renamed product and publisher
                moved = {**cfg["desktop"], **{k: cfg["desktop"][k] + ".moved" for k in ("product_name", "publisher")}}
                refusals["product_name"] = release_desktop.built_wrong(
                    second_seen["desktop"], ver, moved,
                    {**first_seen["desktop"], "product": moved["product_name"], "publisher": moved["publisher"]})
    except BaseException as exc:
        exc.left = left   # the same list: the keys' removal below still reaches it
        raise
    finally:
        # A test key never outlives the rehearsal; one locked file never spares the other. One that cannot be
        # removed is reported in `left`, never raised: it would hide the round's outcome and its repair. The
        # key is disposable and signs nothing that ships, so a repair still starts.
        for store in stores:
            try:
                store.unlink(missing_ok=True)
            except OSError as exc:
                errorlog.record("release-rehearsal-cleanup", exc, repo=str(repo), at=str(store))
                left.append(f"{store}: {exc}")
    # Each declared kind's refusals, each with exactly the complaints its renamed identity must raise.
    expected = [*((("certificate", "서명 인증서", 2), ("application_id", "앱 ID", 2)) if android else ()),
                *((("product_name", "제품 이름", 4), ("product_name", "게시자", 2)) if "desktop" in cfg else ())]
    caught = bool(refusals) and all(len([w for w in refusals.get(k, []) if marker in w]) == want
                                    for k, marker, want in expected)
    out = {"sha": sha, "seed": first, "cert": cert, "folder": str(folder), "rounds": rounds,
           "refusals": refusals, "ok": built and caught, "left": left}
    record = needed and {"id": attempt, "remote": None, "tag": f"v{rounds[-1]['version']}",
                         "version": rounds[-1]["version"], "sha": sha, "base": branch, "repair": {"needed": needed}}
    return out, record or None
