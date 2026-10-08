"""Start only a browser test's own server, preserving its bound socket."""

import threading
import time


def start(server, sock):
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return thread
        time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    raise RuntimeError("Fixture server did not start")
