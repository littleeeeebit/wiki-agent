"""Manual two-turn session continuity check; never run by application startup."""

from pathlib import Path


def demo() -> None:
    """Do two turns continue in one process? That is this file's premise."""

    from .chat_session import ChatSession
    import sys
    import time

    sys.stdout.reconfigure(encoding="utf-8")
    here = Path(__file__).resolve().parents[2]
    chat = ChatSession(here)

    # Measuring the wording of an answer goes red on one turn of phrase. What
    # has to be measured is whether the second turn remembers the first, so it
    # is handed one token to remember and only that is compared.
    token = "MARMOT-77"

    said = []
    t0 = time.time()
    for ev in chat.say(f"이 표를 기억해라: {token}. '알겠다' 한 마디만 답해라."):
        if ev.kind == "done":
            said.append(ev.text)
    first = time.time() - t0

    t1 = time.time()
    for ev in chat.say("방금 기억하라고 한 표를 그대로 적어라. 그것만."):
        if ev.kind == "done":
            said.append(ev.text)
    second = time.time() - t1

    chat.close()
    assert len(said) == 2, said
    assert token in said[1], said[1]  # one process is holding the first turn
    print(f"ok  두 턴이 이어진다 — {first:.1f}s → {second:.1f}s")
    print(f"    1: {said[0][:60]}")
    print(f"    2: {said[1][:60]}")
