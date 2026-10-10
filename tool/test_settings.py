"""Settings written from two places at once keep both changes."""

import json
import threading

from common import settings


def test_the_loop_settings_and_the_translation_switch_written_at_once_both_survive(tmp_path):
    path, start, failed = tmp_path / "main.json", threading.Barrier(2), []

    def write(key):
        start.wait()
        try:
            for n in range(100):
                settings.saved(path, {key: n})
        except OSError as exc:
            failed.append(exc)

    writers = [threading.Thread(target=write, args=(key,)) for key in ("rounds", "translate")]
    [w.start() for w in writers], [w.join() for w in writers]
    assert not failed and json.loads(path.read_text(encoding="utf-8")) == {"rounds": 99, "translate": 99}
