"""Start only a browser test's own server, preserving its bound socket."""

from contextlib import contextmanager
import socket
import threading
import time

import uvicorn


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


@contextmanager
def served(app):
    """`app` on a free local port for the block; yields the port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=1))
    thread = start(server, sock)
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
