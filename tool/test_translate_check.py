"""What `translate.py --check` has to catch, proved against a real git repo.

The check reads originals out of history, so a fixture that fakes git would
only prove the fake works. Each test below builds a throwaway repo, commits a
Korean page, then writes an English one over it and asks what the check says.

The failure this whole file guards is the quiet one: a gate that examined
nothing exits 0 and reads exactly like a gate that examined everything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import translate as T  # noqa: E402

KOREAN = (
    "---\n"
    "scope: craft\n"
    'triggers: ["훅", "인코딩"]\n'
    "---\n"
    "\n"
    "# 훅은 세션을 멈추지 않는다\n"
    "\n"
    "규칙. `sys.stdout.reconfigure` 로 인코딩을 고정한다.\n"
    "나라장터 입찰공고 파서는 그대로 둔다. [[diagnose]] 가 나머지를 든다.\n"
)

ENGLISH = (
    "---\n"
    "scope: craft\n"
    'triggers: ["훅", "인코딩"]\n'
    "---\n"
    "\n"
    "# A hook never stops the session\n"
    "\n"
    "Rule. Pin the encoding with `sys.stdout.reconfigure`.\n"
    "Leave the 나라장터 입찰공고 parser alone. [[diagnose]] carries the rest.\n"
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture(scope="module")
def committed(tmp_path_factory) -> Path:
    """The Korean page committed once, with `[[diagnose]]` real; copied per test."""

    root = tmp_path_factory.mktemp("committed")
    _git(root, "init", "-q")
    (root / "craft").mkdir()
    (root / "craft" / "hooks.md").write_text(KOREAN, encoding="utf-8")
    # The link target lives outside the directory under test. Inside it, it
    # would itself be a target with no manifest entry and every test would
    # fail on that instead of on what it means to check.
    (root / "operator").mkdir()
    (root / "operator" / "diagnose.md").write_text("# diagnose\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "korean")
    return root


@pytest.fixture
def repo(committed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(committed, repo)
    monkeypatch.setattr(T, "ROOT", repo)
    monkeypatch.setattr(T, "REVIEW", repo / "review.md")
    return repo


def _manifest(repo: Path, *rows: dict) -> Path:
    path = repo / "baseline.json"
    path.write_text(json.dumps({"entries": list(rows)}), encoding="utf-8")
    return path


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _entry(repo: Path, **over: object) -> dict:
    row = {
        "output": "craft/hooks.md",
        "kind": "translation",
        "source": "craft/hooks.md",
        "commit": _head(repo),
    }
    row.update(over)
    return row


def test_a_faithful_translation_passes(repo: Path, capsys) -> None:
    manifest = _manifest(repo, _entry(repo))
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 0
    assert "결함 없음" in capsys.readouterr().out


def test_a_file_the_manifest_never_mentions_fails(repo: Path, capsys) -> None:
    """Otherwise a page is translated and then exempted by being left out."""

    manifest = _manifest(repo)
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "manifest 에 없다" in capsys.readouterr().out


def test_an_empty_target_set_fails(repo: Path, capsys) -> None:
    """A gate that checked nothing must not read like one that checked all."""

    manifest = _manifest(repo, _entry(repo))
    assert T.check(["docs/plans"], manifest, None, 0) == 1
    assert "검사 대상이 없다" in capsys.readouterr().out


def test_a_changed_command_fails(repo: Path, capsys) -> None:
    manifest = _manifest(repo, _entry(repo))
    (repo / "craft" / "hooks.md").write_text(
        ENGLISH.replace("`sys.stdout.reconfigure`", "`sys.stdout.configure`"),
        encoding="utf-8",
    )

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "code 가 원문과 다르다" in capsys.readouterr().out


def test_a_lost_keep_korean_term_fails(repo: Path, capsys) -> None:
    manifest = _manifest(repo, _entry(repo))
    (repo / "craft" / "hooks.md").write_text(
        ENGLISH.replace("나라장터", "KONEPS"), encoding="utf-8"
    )

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "보존 용어가 사라졌다" in capsys.readouterr().out


def test_a_changed_trigger_fails_a_plain_translation(repo: Path, capsys) -> None:
    """Triggers match the user's Korean utterance. Translating one kills it."""

    manifest = _manifest(repo, _entry(repo))
    (repo / "craft" / "hooks.md").write_text(
        ENGLISH.replace('["훅", "인코딩"]', '["hook", "encoding"]'), encoding="utf-8"
    )

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "front_matter 가 원문과 다르다" in capsys.readouterr().out


def test_a_rewrite_may_waive_front_matter_but_still_owes_its_commands(
    repo: Path, capsys
) -> None:
    """Waiving one category exempts that category and nothing else."""

    manifest = _manifest(
        repo,
        _entry(repo, kind="rewrite", allow=["front_matter"], why="규칙이 뒤집혔다"),
    )
    changed = ENGLISH.replace('["훅", "인코딩"]', '["hook", "encoding"]')
    (repo / "craft" / "hooks.md").write_text(changed, encoding="utf-8")
    assert T.check(["craft"], manifest, None, 0) == 0

    (repo / "craft" / "hooks.md").write_text(
        changed.replace("`sys.stdout.reconfigure`", "`nope`"), encoding="utf-8"
    )
    assert T.check(["craft"], manifest, None, 0) == 1
    assert "code 가 원문과 다르다" in capsys.readouterr().out


def test_a_waiver_without_a_reason_fails(repo: Path, capsys) -> None:
    manifest = _manifest(repo, _entry(repo, kind="rewrite", allow=["front_matter"]))
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "why 에 이유를 적는다" in capsys.readouterr().out


def test_a_renamed_page_compares_against_the_old_path(repo: Path, capsys) -> None:
    """A rename is stated, never guessed. Guessing is how the wrong file wins."""

    manifest = _manifest(
        repo, _entry(repo, output="craft/fail-open.md", source="craft/hooks.md")
    )
    (repo / "craft" / "hooks.md").unlink()
    (repo / "craft" / "fail-open.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 0
    assert "결함 없음" in capsys.readouterr().out


def test_an_original_missing_from_history_fails(repo: Path, capsys) -> None:
    """A shallow clone without the pinned commit must say so, not pass."""

    manifest = _manifest(repo, _entry(repo, commit="0" * 40))
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "원문을 못 읽는다" in capsys.readouterr().out


def test_a_moving_reference_is_rejected(repo: Path, capsys) -> None:
    """A branch name or a short sha moves, and then the baseline is not one."""

    manifest = _manifest(repo, _entry(repo, commit="HEAD"))
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "40자리 전체 SHA" in capsys.readouterr().out


def test_a_new_document_needs_no_original(repo: Path, capsys) -> None:
    manifest = _manifest(
        repo, _entry(repo), {"output": "craft/mirror.md", "kind": "new"}
    )
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")
    (repo / "craft" / "mirror.md").write_text(
        "# Mirror\n\nRule. Run it in a second cell. [[diagnose]]\n", encoding="utf-8"
    )

    assert T.check(["craft"], manifest, None, 0) == 0
    assert "결함 없음" in capsys.readouterr().out


def test_a_new_document_still_owes_its_links(repo: Path, capsys) -> None:
    manifest = _manifest(
        repo, _entry(repo), {"output": "craft/mirror.md", "kind": "new"}
    )
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")
    (repo / "craft" / "mirror.md").write_text(
        "# Mirror\n\nRule. [[no-such-page]]\n", encoding="utf-8"
    )

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "깨진 링크 [[no-such-page]]" in capsys.readouterr().out


def test_a_document_kept_in_korean_is_declared_rather_than_left_out(
    repo: Path, capsys
) -> None:
    """Leaving it out of the manifest is how thirteen files went unchecked.

    Some documents stay Korean on purpose — the install guides a person
    follows at their own machine. There was no kind for that, so they were
    simply absent, and absent reads the same as forgotten. It is now said out
    loud, and the comparison that has no English output to make is skipped.
    """

    manifest = _manifest(
        repo,
        _entry(repo),
        {"output": "craft/setup.md", "kind": "kept",
         "why": "팀원이 자기 PC 에서 따라 하는 설치 안내다."},
    )
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")
    (repo / "craft" / "setup.md").write_text(
        "# 설치\n\n규칙. 자기 계정으로 로그인한다.\n", encoding="utf-8"
    )

    assert T.check(["craft"], manifest, None, 0) == 0
    assert "결함 없음" in capsys.readouterr().out


def test_keeping_a_document_korean_still_owes_a_reason(repo: Path, capsys) -> None:
    manifest = _manifest(
        repo, _entry(repo), {"output": "craft/setup.md", "kind": "kept"}
    )
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")
    (repo / "craft" / "setup.md").write_text("# 설치\n", encoding="utf-8")

    assert T.check(["craft"], manifest, None, 0) == 1
    assert "why" in capsys.readouterr().out


def test_source_root_says_it_is_not_a_gate_pass(repo: Path, capsys) -> None:
    """Pre-commit work can be checked, but it must not read as shipped."""

    spare = repo / "snapshot"
    (spare / "craft").mkdir(parents=True)
    (spare / "craft" / "hooks.md").write_text(KOREAN, encoding="utf-8")

    manifest = _manifest(repo, _entry(repo, commit="0" * 40))
    (repo / "craft" / "hooks.md").write_text(ENGLISH, encoding="utf-8")

    assert T.check(["craft"], manifest, spare, 0) == 0
    assert "배포 게이트 통과로 세지 않는다" in capsys.readouterr().out
