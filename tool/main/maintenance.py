"""Prepare wiki documents on the PR branch after an explicit merge request."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import corpus
import debt
import lint
import repo_graph
import repo_lint

from . import architecture, query, runtime, specs, work


def git(path: Path, *args: str) -> str:
    done = specs.sh(["git", *args], path, 120)
    if done.returncode:
        raise ValueError(specs.said(done))
    return done.stdout if "-z" in args else done.stdout.strip()


def changes(path: Path) -> set[str]:
    names = set(git(path, "diff", "--name-only", "HEAD", "-z").split("\0"))
    names.update(git(path, "diff", "--cached", "--name-only", "-z").split("\0"))
    names.update(git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return names - {""}


def outputs(path: Path) -> dict:
    names = changes(path) | {name for name in (".wiki/corpus.json", ".wiki/graph.json") if (path / name).exists()}
    return {"files": {name: hashlib.sha256((path / name).read_bytes()).hexdigest()
                      if (path / name).is_file() else None for name in sorted(names)},
            "working": hashlib.sha256(git(path, "diff", "HEAD", "--binary").encode("utf-8")).hexdigest(),
            "staged": hashlib.sha256(git(path, "diff", "--cached", "--binary").encode("utf-8")).hexdigest()}


def findings(repo: Path, path: Path) -> list[tuple[str, str]]:
    if repo.resolve() == query.ROOT.resolve():
        # Machine wiring belongs to the original checkout, not a linked PR tree.
        return [f for f in lint.check(path, adapters=repo / "adapters")[2] if f[0] != "모순(슬롯)"]
    return repo_lint.check(path, wiring=False)


def indexes(path: Path) -> list[str]:
    published = set(git(path, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0"))
    docs = [doc for doc in corpus.collect(path, corpus.DEFAULT_ROOTS) if doc["path"] in published]
    if not docs and not (path / ".wiki/corpus.json").exists():
        return []
    target = path / ".wiki/corpus.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    architecture.write_changed(target, json.dumps({"docs": docs}, ensure_ascii=False))
    repo_graph.write(path)
    return [".wiki/corpus.json", ".wiki/graph.json"]


def repair(path: Path, spec: dict, problems: list[tuple[str, str]]) -> None:
    if spec.get("implementation_environment", "local") != "local":
        raise ValueError("위키 lint 수정은 외부 구현자가 해야 한다 — " + json.dumps(problems, ensure_ascii=False))
    chosen = spec.get("cell") or {}
    chat = work.session(path, chosen.get("model", ""), chosen.get("effort", ""), chosen.get("fast", False))
    prompt = ("The person clicked Merge. Fix these wiki lint findings on this PR branch. "
              "Edit only Markdown documents; preserve rules and their evidence. Do not edit code, "
              "commit, push, merge, delegate or start review. The server rebuilds the indexes, "
              "commits the maintenance and requests review afterwards. Explain what you fixed.\n\n"
              + json.dumps(problems, ensure_ascii=False))
    run = work.Run(chat)
    ended = threading.Event()

    def cancel():
        while not ended.wait(0.1):
            if runtime.stopping.is_set():
                run.halt.set()
            if run.halt.is_set():
                chat.stop(run.halt)
                return

    watcher = threading.Thread(target=cancel, daemon=True)
    watcher.start()
    final, failed = "", ""
    with query._lock:
        work._runs[str(path)] = run
    try:
        work.remember(path, "user", prompt)
        work.feed.put({"kind": "started", "path": str(path), "turn": run.turn})
        for ev in chat.say(prompt, run.halt):
            run.put({"kind": ev.kind, "text": ev.text, "meta": ev.meta,
                     "session_id": chat.id, "parent_id": None})
            if ev.kind == "done":
                final = ev.text
            if ev.kind == "error" or ev.meta.get("error"):
                failed = ev.text or "위키 수정 실패"
        if failed or not final.strip() or run.halt.is_set():
            raise ValueError(failed or "위키 수정이 완료되지 않았다")
    finally:
        ended.set()
        watcher.join()
        try:
            work.remember(path, "assistant", final, error=failed, steps=work.steps(run.events),
                          turn=run.turn, cell=chat.id, started_at=run.started_at)
        finally:
            run.finish()
            work.feed.put({"kind": "work-record", "path": str(path)})


def prepare(repo: Path, path: Path, spec: dict, head: str, base: str) -> str:
    """Return the prepared head; persist a receipt before pushing for safe retry.

    The caller reserves this checkout. Only documents and known generated files
    can enter the maintenance commit; pre-existing edits are never included.
    """
    receipt = spec.get("maintenance") or {}
    identity = {"rev": spec["rev"], "base": base, "pr": spec["pr"]["number"]}
    local = git(path, "rev-parse", "HEAD")
    resumable = all(receipt.get(k) == v for k, v in identity.items()) and head in (
        receipt.get("source_head"), receipt.get("head"))
    if local != head and not (resumable and receipt.get("head") == local):
        raise ValueError("작업 폴더가 리뷰한 커밋에 있지 않다")
    retry = resumable and receipt.get("state") == "interrupted" and receipt.get("outputs") == outputs(path)
    if (git(path, "status", "--porcelain") or resumable and receipt.get("state") == "interrupted") and not retry:
        raise ValueError("커밋하지 않은 변경이 있다 — 보존한 뒤 머지를 다시 요청하세요")
    branch = git(path, "branch", "--show-current")
    if branch and branch != specs.branch_of(spec):
        raise ValueError("작업 폴더가 PR 브랜치에 있지 않다")
    if resumable and receipt.get("state") in ("committed", "complete") and receipt.get("head") == local:
        if receipt["state"] == "committed":
            specs.merge_progress(spec["repo"], spec["id"], "같은 PR에 준비 커밋 다시 올리는 중")
            git(path, "push", "origin", f"HEAD:refs/heads/{specs.branch_of(spec)}")
            specs.update(spec["repo"], spec["id"], maintenance={**receipt, "state": "complete"})
        return local
    receipt = {**identity, "source_head": head, "head": head, "state": "preparing"}
    specs.update(spec["repo"], spec["id"], maintenance=receipt)
    try:
        specs.merge_progress(spec["repo"], spec["id"], "위키 색인·부채 기준선 새로 고치는 중")
        ratcheted = debt.tighten(path)   # only lowers: what this PR shrank stays shrunk
        generated = indexes(path) + ratcheted
        specs.merge_progress(spec["repo"], spec["id"], "위키 lint 확인 중")
        problems = findings(repo, path)
        if problems:
            specs.merge_progress(spec["repo"], spec["id"], "위키 lint 수정 중")
            repair(path, spec, problems)
            specs.merge_progress(spec["repo"], spec["id"], "수정한 위키 lint·색인 다시 확인 중")
            generated = indexes(path) + ratcheted
            remaining = findings(repo, path)
            if remaining:
                raise ValueError("위키 lint 수정이 끝나지 않았다 — " + json.dumps(remaining, ensure_ascii=False))
        if (path / ".omm").is_dir():
            specs.merge_progress(spec["repo"], spec["id"], ".omm 구조 문서 새로 고치는 중")
            architecture.scan(path, model=(spec.get("cell") or {}).get("model") or None, halt=runtime.stopping)
        if runtime.stopping.is_set():
            raise ValueError("서버 종료로 머지 준비가 중단됐다")
        if git(path, "rev-parse", "HEAD") != head:
            raise ValueError("위키 수정 중 커밋이 바뀌었다 — 새 리뷰가 필요하다")
        changed = changes(path)
        unexpected = [name for name in changed if name not in generated
                      and not (name.endswith(".md") or name.startswith(".omm/"))]
        if unexpected:
            raise ValueError("위키 수정이 코드까지 변경했다 — 머지 중단: " + ", ".join(sorted(unexpected)))
        if changed:
            git(path, "--literal-pathspecs", "add", "--", *sorted(changed))
        if generated:
            git(path, "--literal-pathspecs", "add", "-f", "--", *generated)
        if git(path, "diff", "--cached", "--name-only"):
            specs.merge_progress(spec["repo"], spec["id"], "위키·색인·구조 문서 커밋 중")
            git(path, "commit", "-m", "Refresh wiki and architecture before merge")
        prepared = git(path, "rev-parse", "HEAD")
        receipt = {**receipt, "head": prepared, "state": "committed", "files": sorted(changed | set(generated))}
        specs.update(spec["repo"], spec["id"], maintenance=receipt)
    except Exception:
        # Normal stop/shutdown and failed tools retain exact output fingerprints.
        # A later click may retry them; changed files still need user preservation.
        specs.update(spec["repo"], spec["id"], maintenance={**receipt, "state": "interrupted", "outputs": outputs(path)})
        raise
    if prepared != head:
        specs.merge_progress(spec["repo"], spec["id"], "같은 PR에 준비 커밋 올리는 중")
        git(path, "push", "origin", f"HEAD:refs/heads/{specs.branch_of(spec)}")
    specs.update(spec["repo"], spec["id"], maintenance={**receipt, "state": "complete"})
    return prepared
