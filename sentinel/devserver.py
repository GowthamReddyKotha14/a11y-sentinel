"""Tiny threaded static file server for the bundled demo site and tests."""
from __future__ import annotations

import contextlib
import functools
import http.server
import threading
from pathlib import Path

DEMO_SITE = Path(__file__).resolve().parent.parent / "demo_site"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test/demo output clean
        pass


@contextlib.contextmanager
def serve(directory: Path = DEMO_SITE, port: int = 0):
    handler = functools.partial(_Quiet, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
