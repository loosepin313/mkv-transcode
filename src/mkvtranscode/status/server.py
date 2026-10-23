"""Serve the status page and its JSON snapshot.

The page has no login. Bind it to 127.0.0.1, or to an address on your local
network, and keep it off the public internet. The server only reads files and
serves them. It writes nothing.
"""

from __future__ import annotations

import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from ..log import get_logger
from .collector import job_detail, snapshot

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

# The page loads only its own files, so a strict policy costs nothing.
CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; font-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'")

log = get_logger("status")


class Handler(BaseHTTPRequestHandler):
    """Serve the page, the assets, and one JSON endpoint."""

    server_version = "mkv-transcode-status"
    config = None            # set by serve()
    interval = 5.0
    recent = 80

    # ---- helpers -------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fail(self, code: int, message: str) -> None:
        self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8")

    def asset(self, name: str):
        """Return the bytes of an asset inside the package web folder."""
        full = os.path.realpath(os.path.join(WEB_DIR, name.lstrip("/")))
        base = os.path.realpath(WEB_DIR)
        if not full.startswith(base + os.sep):
            return None, 404
        if not os.path.isfile(full):
            return None, 404
        ext = os.path.splitext(full)[1].lower()
        try:
            with open(full, "rb") as handle:
                return handle.read(), 200
        except OSError:
            return None, 500

    # ---- routes --------------------------------------------------------

    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            body, code = self.asset("index.html")
            if body is None:
                self._fail(code, "index.html is missing from the package")
                return
            self._send(200, body, CONTENT_TYPES[".html"])
            return
        if path == "/api/snapshot":
            try:
                data = snapshot(self.config, recent_limit=self.recent)
                data["interval"] = self.interval
            except Exception as exc:                     # keep the page alive
                log.warning("snapshot failed: %s", exc)
                data = {"error": str(exc)}
            body = json.dumps(data, indent=1, sort_keys=True).encode("utf-8")
            self._send(200, body, CONTENT_TYPES[".json"])
            return
        if path.startswith("/api/job"):
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            name = ""
            for part in query.split("&"):
                if part.startswith("name="):
                    name = unquote(part[5:])
            try:
                data = job_detail(self.config, name)
            except Exception as exc:
                log.warning("job detail failed: %s", exc)
                data = {"found": False, "reason": str(exc)}
            body = json.dumps(data, indent=1, sort_keys=True).encode("utf-8")
            self._send(200, body, CONTENT_TYPES[".json"])
            return
        if path == "/api/health":
            self._send(200, b"ok\n", CONTENT_TYPES[".txt"])
            return
        body, code = self.asset(path)
        if body is None:
            self._fail(code, "not found")
            return
        ext = os.path.splitext(path)[1].lower()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_HEAD(self) -> None:
        self.do_GET()

    def log_message(self, fmt, *args) -> None:
        """Send access lines to the app logger instead of stderr."""
        log.debug("%s - %s", self.address_string(), fmt % args)


def make_server(config, host: str, port: int, interval: float = 5.0, recent: int = 80):
    """Build the HTTP server without starting it."""
    handler = type("BoundHandler", (Handler,),
                   {"config": config, "interval": interval, "recent": recent})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(config, host: str = "127.0.0.1", port: int = 8090, interval: float = 5.0,
          recent: int = 80) -> int:
    """Run the status server until SIGTERM or SIGINT arrives."""
    try:
        httpd = make_server(config, host, port, interval, recent)
    except OSError as exc:
        print("cannot listen on %s:%d: %s" % (host, port, exc))
        return 1
    stop = threading.Event()

    def _stop(signum, frame) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    print("mkv-transcode status page on http://%s:%d (refresh every %ds)"
          % (shown, port, int(interval)))
    try:
        httpd.serve_forever(poll_interval=0.4)
    finally:
        httpd.server_close()
    print("status page stopped")
    return 0
