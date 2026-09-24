"""Install, injection and the health check all read the checkout's adapter
rather than the project name."""

import json
from pathlib import Path
import sys
import tempfile

import apply
from wiki import match as inject


def test_local_adapter():
    with tempfile.TemporaryDirectory(prefix="adapter 한글 ") as tmp:
        for parent, value in (("one", "첫 checkout"), ("two", "둘째 checkout")):
            project = Path(tmp) / parent / "same name"
            local = project / ".wiki"
            local.mkdir(parents=True)
            (local / "adapter.toml").write_text(
                'agents=["claude"]\n[slots]\nreview_dir="' + value + '"\nrule_budget=1234\n',
                encoding="utf-8", newline="\n",
            )
            assert inject.slots_for(None, project)["review_dir"] == value
            assert inject.slots_for("example", project)["review_dir"] == value
            assert inject.budget(None, inject.RULE_BUDGET, project) == 1234
            assert "review_dir" not in apply.unfilled(None, project)
            assert apply.wiring_drift(project), "설정 전체 누락도 찾아야 한다"
            settings = {}
            apply.configure(settings, project, None, sys.executable, "claude")
            target = project / ".claude/settings.json"
            target.parent.mkdir()
            target.write_text(json.dumps(settings), encoding="utf-8")
            assert not apply.wiring_drift(project)
            (local / "installed-agents.json").write_text('["claude"]', encoding="utf-8")
            original = (local / "adapter.toml").read_bytes()
            (local / "adapter.toml").write_bytes(b'[broken')
            assert apply.wiring_drift(project), "설치 기록이 있어도 망가진 adapter를 감추지 않는다"
            (local / "adapter.toml").write_bytes(original)
            target.unlink()
            assert apply.wiring_drift(project), "설치 기록은 설정 전체 누락을 찾는다"
            target.write_text(json.dumps(settings), encoding="utf-8")
            command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            assert "--adapter" not in command
            moved = project.with_name("renamed 이름")
            project.rename(moved)
            assert inject.slots_for(None, moved)["review_dir"] == value
            assert apply.wiring_drift(moved), "이동 후 이전 절대 경로는 드리프트다"
            (moved / ".wiki/adapter.toml").unlink()
            assert inject.slots_for(None, moved) == {}, "로컬 원본 누락을 허브의 동명 adapter로 숨기지 않는다"


if __name__ == "__main__":
    test_local_adapter()
