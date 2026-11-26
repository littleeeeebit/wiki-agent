"""oneshot step 12: pull request #1, the backend's draft, opened once its build passes.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The built HEAD is pushed and the run's
pull request opened as a draft (`published`), or found and its body brought up to date. The measurement
(`oneshot_measure`) runs on the HEAD it shows, its approval is commented there, and the final stage makes it
ready.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException

from . import oneshot, oneshot_spec, prerequisite_review, specs, work


def body(spec: dict, head: str) -> str:
    o = spec["oneshot"]
    lines = ["원샷이 만든 백엔드의 첫 PR 이다. 아직 초안이다.", "",
             f"- 빌드 커밋: `{head}`",
             "- 이 PR 에서 진짜 환경 실측과 백엔드 리뷰를 한다. 주인이 승인한 실측 기록은 이 PR 의 댓글로 붙는다.",
             "- 화면을 만든 뒤 마지막 단계에서 설명을 다시 쓰고 초안을 푼다.", "", "## 요구", ""]
    lines += [f"- {r['id']}: {r['text']}" for r in spec.get("requirements") or []]
    if o.get("measures"):
        lines += ["", f"실측 {len(o['measures'])}회 · 리뷰 {len(o.get('reviews') or [])}회"]
    return "\n".join(lines)


def unready(spec: dict, path: Path, halt=None) -> str:
    """Why the branch may not be pushed now, or empty: once PR #1 exists, a push goes up only while that draft is
    the branch's open pull request, since a push moves whichever one is open on it. The lookup at the pull request
    write checks the number again, for a draft closed while the push ran. A pull request open on the branch that
    is ready (marked by the owner, or made ready by a run whose `done` was lost) is made a draft again first: a
    head not yet checked never goes up on a ready one."""

    n = (spec["oneshot"].get("pr1") or {}).get("number")
    try:   # the draft's own base (`specs.own_base`), read off GitHub only for a record without one
        base = specs.base_of(spec, path)
        if base.returncode or not base.stdout.strip():
            return f"기본 브랜치를 모른다 — {specs.said(base)}"
        if found := specs.existing_pr(path, specs.branch_of(spec), base.stdout.strip(), n):
            return drafted(path, found[0], halt)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:   # every one a stop with its reason
        return str(exc) if type(exc) is RuntimeError else f"PR 을 확인하지 못했다 — {type(exc).__name__}: {exc}"
    return ""


def drafted(path: Path, n: int, halt=None) -> str:
    """Pull request `n` read off GitHub and made a draft again if it is ready, then read back: "" once GitHub says
    it is a draft, else why not (a stop set while it was read changes nothing)."""

    def draft() -> bool:
        seen = specs.sh(["gh", "pr", "view", str(n), "--json", "isDraft"], path, 60)
        if seen.returncode:
            raise ValueError(specs.said(seen))
        return bool(json.loads(seen.stdout).get("isDraft"))

    try:
        if draft():
            return ""
        if halt is not None and halt.is_set():   # a Stop while it was read: nothing on GitHub changes after it
            return "사람이 멈춤 — PR 을 바꾸지 않았다"
        undo = specs.sh(["gh", "pr", "ready", str(n), "--undo"], path, 60)
        if undo.returncode or not draft():
            return f"PR #{n} 을 초안으로 되돌리지 못했다 — {specs.said(undo) if undo.returncode else '아직 준비 완료다'}"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"PR #{n} 이 초안인지 확인하지 못했다 — {type(exc).__name__}: {exc}"
    return ""


def published(repo: str) -> dict:
    """The built HEAD pushed, and the run's pull request opened as a draft, or found and its body brought up to
    date; `ValueError` says why not. The final stage reuses it (`specs.pull_request` finds it) and makes it ready."""

    spec = oneshot_spec.spec_of(repo)
    path, head, branch = Path(spec["worktree"]), spec["oneshot"]["built_head"], specs.branch_of(spec)
    if why := unready(spec, path):
        raise ValueError(why)
    pushed =prerequisite_review.push(path, spec, ["git", "push", "-u", "origin", f"{head}:refs/heads/{branch}"])
    if pushed.returncode:
        raise ValueError(f"push 실패 — {specs.said(pushed)}")
    base = specs.base_of(spec, path)
    if base.returncode or not base.stdout.strip():
        raise ValueError(f"기본 브랜치를 모른다 — {specs.said(base)}")
    base = base.stdout.strip()
    title = f"[초안] 백엔드 — {' '.join(spec['goal'].split())}"
    title = title if len(title) <= 200 else title[:199] + "…"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body(spec, head))
    try:
        # `RuntimeError` when GitHub cannot be read, or a repair's draft is not the one measured and reviewed
        found = specs.existing_pr(path, branch, base, (spec["oneshot"].get("pr1") or {}).get("number"))
        if found:
            done = specs.sh(["gh", "pr", "edit", str(found[0]), "--body-file", fh.name], path, 120)
            if done.returncode:
                raise ValueError(f"PR #{found[0]} 의 설명을 고치지 못했다 — {specs.said(done)}")
        else:
            done = specs.sh(["gh", "pr", "create", "--draft", "--base", base, "--head", branch, "--title", title,
                             "--body-file", fh.name], path, 120)
            if m := re.search(r"/pull/(\d+)", done.stdout or ""):
                found = (int(m[1]), done.stdout.strip().splitlines()[-1])
            else:
                found = specs.existing_pr(path, branch, base)   # a create whose reply was lost
        if not found:
            raise ValueError(f"PR 을 만들지 못했다 — {specs.said(done)}")
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"PR 을 만들거나 고치지 못했다 — {exc}") from None
    finally:
        os.unlink(fh.name)
    pr = {"number": found[0], "url": found[1], "branch": branch, "base": base, "head": head}
    oneshot_spec.saved(repo, oneshot={"pr1": pr})
    return pr


def begin(repo: str) -> None:
    """After the build passed: the HEAD goes up to pull request #1, then the server measures it by itself."""

    try:
        oneshot.start_worker(oneshot_spec.store(repo), "publish", publish, (repo,), {})
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 PR #1 을 올리지 못했다")


def publish(repo: str) -> None:
    try:
        pr = published(repo)
    except ValueError as exc:
        oneshot_spec.store(repo).stop("measure", f"PR #1 을 올리지 못했다 — {exc} — [실측 시작]을 누르면 다시 올린다")
        return
    spec = oneshot_spec.saved(repo, oneshot={"working": None})
    from . import oneshot_measure   # it imports this module
    try:   # the server measures by itself at once (plan 13): the owner is called once a machine has nothing to find
        oneshot_measure.start(repo, oneshot_measure.Start(head=spec["oneshot"]["built_head"]))
    except HTTPException as exc:   # an input not given yet, or the run moved on: the owner's `[실측 시작]` starts it
        work.notice(f"PR #{pr['number']} 초안 · 실측을 기다린다", f"{repo} · {exc.detail} — 입력을 넣고 [실측 시작]을 누른다")


def commented(spec: dict, record: dict) -> str:
    """The approved measurement as a comment on pull request #1, so the review's record is the PR's; why it could
    not be posted, or empty. The run goes on either way: the record is the spec's, the comment its copy."""

    pr = spec["oneshot"].get("pr1")
    if not pr:
        return "PR #1 이 없다"
    if pr.get("head") != record["head"] or spec["oneshot"].get("built_head") != record["head"]:
        return f"PR #1 이 승인한 HEAD `{record['head'][:12]}` 를 보이지 않는다 — 실측 기록은 원샷 화면에만 있다"
    rows = [f"| {m['what']} | {m['value']}{m['unit']} | {m['pass']['op']} {m['pass']['value']} | {'통과' if m['ok'] else '실패'} |"
            for m in record.get("metrics") or []]
    text = "\n".join([f"## 실측 {record['n']} · `{record['head'][:12]}` · 주인 승인", "",
                      "| 측정 | 값 | 기준 | 결과 |", "| --- | --- | --- | --- |", *rows, "",
                      f"주인 메모: {record['owner']['note'] or '(없음)'}", "",
                      f"스크립트 메모: {record.get('notes') or '(없음)'}"])
    body = None
    try:   # GitHub's own head, read just before: a push from elsewhere since PR #1 was published is not approved
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
            body = fh.name   # a failed write is a failed comment too: the review starts all the same
            fh.write(text)
        now = specs.sh(["gh", "pr", "view", str(pr["number"]), "--json", "headRefOid", "-q", ".headRefOid"],
                       Path(spec["worktree"]), 60)
        if now.returncode or now.stdout.strip() != record["head"]:
            return f"PR #{pr['number']} 이 지금 승인한 HEAD `{record['head'][:12]}` 를 보이지 않는다 — {specs.said(now)}"
        done = specs.sh(["gh", "pr", "comment", str(pr["number"]), "--body-file", body], Path(spec["worktree"]), 60)
        return f"PR #{pr['number']} 에 실측 기록을 달지 못했다 — {specs.said(done)}" if done.returncode else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"PR #{pr['number']} 에 실측 기록을 달지 못했다 — {type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(OSError):   # a temporary file left behind is no failed comment
            body and os.unlink(body)
