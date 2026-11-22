"""oneshot step 12: pull request #1, the backend's draft, opened once its build passes.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The built HEAD is pushed and the run's
pull request opened as a draft (`published`), or found and its body brought up to date. The measurement
(`oneshot_measure`) runs on the HEAD it shows, its approval is commented there, and the final stage makes it
ready.
"""

from __future__ import annotations

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


def published(repo: str) -> dict:
    """The built HEAD pushed, and the run's pull request opened as a draft, or found and its body brought up to
    date; `ValueError` says why not. The final stage reuses it (`specs.pull_request` finds it) and makes it ready."""

    spec = oneshot_spec.spec_of(repo)
    path, head, branch = Path(spec["worktree"]), spec["oneshot"]["built_head"], specs.branch_of(spec)
    pushed = prerequisite_review.push(path, spec, ["git", "push", "-u", "origin", f"{head}:refs/heads/{branch}"])
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
        found = specs.existing_pr(path, branch, base)   # `RuntimeError` when GitHub cannot be read
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
    """After the build passed: the HEAD goes up to pull request #1, then the run waits for the owner's start."""

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
    oneshot_spec.saved(repo, oneshot={"working": None})
    work.notice(f"PR #{pr['number']} 초안 · 실측을 기다린다", f"{repo} · 입력을 넣고 [실측 시작]을 누른다")
