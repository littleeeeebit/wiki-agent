"""Start only a browser test's own server, preserving its bound socket."""

import socket
import threading
import time
from contextlib import contextmanager


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
    """`app` on a free local port for the block; yields its base URL."""

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=1))
    thread = start(server, sock)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def page_of(browser, errors: list, **options):
    """A page that records uncaught errors in `errors` and never fetches web fonts."""

    page = browser.new_page(**options)
    page.on("pageerror", lambda error: errors.append(str(error)))
    for fonts in ("https://fonts.googleapis.com/**", "https://fonts.gstatic.com/**"):
        page.route(fonts, lambda route: route.abort())
    return page
