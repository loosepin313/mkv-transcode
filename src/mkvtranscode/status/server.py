"""Serve the status page and its JSON snapshot.

The page has no login. Bind it to 127.0.0.1, or to an address on your local
network, and keep it off the public internet. The server reads files and
serves them. It writes only where you turn a write route on: uploads with
`--allow-upload`, and the two control actions with `--allow-control`.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict
from urllib.parse import unquote

from .. import control
from ..log import get_logger
from ..state import RUNNING, State
from ..util import free_bytes
from .collector import imdb_search, job_detail, safe_under, snapshot, state_path

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# kind -> (config folder, allowed extensions) for the download route.
KINDS = {
    "output": ("transcoded", (".mkv", ".mp4", ".m4v")),
    "nfo": ("transcoded", (".nfo",)),
    "log": ("logs", (".log",)),
    "report": ("logs", (".json",)),
}

UPLOAD_CHUNK = 1 << 20

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
    allow_upload = False
    max_upload_bytes = 0
    allow_control = False

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

    def _headers(self, code: int, content_type: str, length: int) -> None:
        """Send the status line and the shared security headers."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()

    def _query(self) -> Dict:
        """Parse the query string into a dict."""
        raw = self.path.split("?", 1)[1] if "?" in self.path else ""
        out = {}
        for part in raw.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                out[key] = unquote(value)
        return out

    def _send_file(self, full: str) -> None:
        """Stream one file as an attachment."""
        name = os.path.basename(full).replace('"', "").replace("\n", "").replace("\r", "")
        try:
            size = os.path.getsize(full)
        except OSError:
            self._fail(500, "cannot read the file")
            return
        self._headers(200, "application/octet-stream", size)
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        if self.command == "HEAD":
            return
        try:
            with open(full, "rb") as handle:
                while True:
                    chunk = handle.read(UPLOAD_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, BrokenPipeError):
            log.debug("download stopped: %s", full)

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
                data = snapshot(self.config, recent_limit=self.recent,
                                allow_upload=self.allow_upload,
                                max_upload_bytes=self.max_upload_bytes,
                                allow_control=self.allow_control)
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
        if path.startswith("/api/imdb"):
            params = self._query()
            try:
                limit = int(params.get("limit") or 20)
            except ValueError:
                limit = 20
            try:
                data = imdb_search(self.config, params.get("q", ""), limit)
            except Exception as exc:                      # keep the page alive
                log.warning("imdb search failed: %s", exc)
                data = {"query": params.get("q", ""), "results": [], "note": str(exc)}
            body = json.dumps(data, indent=1, sort_keys=True).encode("utf-8")
            self._send(200, body, CONTENT_TYPES[".json"])
            return
        if path.startswith("/api/file"):
            params = self._query()
            self.send_download(params.get("kind", ""), params.get("path", ""))
            return
        body, code = self.asset(path)
        if body is None:
            self._fail(code, "not found")
            return
        ext = os.path.splitext(path)[1].lower()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_PUT(self) -> None:
        """curl -T sends PUT, so accept the same body here."""
        if self.path.split("?", 1)[0] != "/api/upload":
            self._fail(405, "only /api/upload takes a body")
            return
        self.do_POST()

    # ---- control actions ------------------------------------------------

    def _control_enabled(self) -> bool:
        """Return True when the control routes are on. Otherwise answer 403."""
        if self.allow_control:
            return True
        self._fail(403, "controls are off. Start the server with --allow-control.")
        return False

    def _json(self, payload: Dict, code: int = 200) -> None:
        """Send one JSON body."""
        self._send(code, json.dumps(payload, sort_keys=True).encode("utf-8"),
                   CONTENT_TYPES[".json"])

    def handle_process_now(self) -> None:
        """Write a request that tells the watcher to start the queue now.

        The server does not run an encode itself. One process owns the GPU
        pool and the running-job count, and that process is the watcher.
        """
        if not self._control_enabled():
            return
        payload = control.request_process_now(self.config)
        log.info("process now requested by %s", self.address_string())
        self._json({
            "ok": True,
            "action": payload["action"],
            "requested": payload["requested"],
            "expires": payload["expires"],
            "request_file": control.request_path(self.config),
        })

    def handle_clear_jobs(self) -> None:
        """Delete the job rows, so the next scan sees incoming as new work.

        A running job would lose its row when it finished, so the route
        refuses while one runs. The route touches no video, log, or report file.
        """
        if not self._control_enabled():
            return
        path = state_path(self.config)
        if not os.path.isfile(path):
            self._json({"ok": True, "removed": 0, "state_file": path})
            return
        try:
            state = State(path)
        except sqlite3.Error as exc:
            log.warning("cannot open the state file for writing: %s", exc)
            self._fail(500, "the server cannot write %s. Run it with write access "
                            "to the library folder." % path)
            return
        try:
            if state.counts().get(RUNNING, 0):
                self._fail(409, "a job runs. Clear the list after it stops.")
                return
            removed = state.clear_jobs()
        except sqlite3.Error as exc:
            log.warning("clear jobs failed: %s", exc)
            self._fail(500, "the server could not clear the job list: %s" % exc)
            return
        finally:
            state.close()
        log.info("job list cleared by %s: %d row(s)", self.address_string(), removed)
        self._json({"ok": True, "removed": removed, "state_file": path})

    # ---- upload --------------------------------------------------------

    def check_upload_name(self, name: str):
        """Return (ok, message) for one upload name."""
        if not name or name in (".", "..") or name != os.path.basename(name):
            return False, "the name must be a bare file name"
        if len(name) > 200:
            return False, "the name is too long"
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            return False, "the name has control characters"
        allowed = ["." + ext for ext in self.config.watch.extensions]
        ext = os.path.splitext(name)[1].lower()
        if allowed and ext not in allowed:
            return False, "only these extensions are accepted: " + ", ".join(allowed)
        return True, ""

    def do_POST(self) -> None:
        """Route one POST: an upload body, or one control action."""
        path = self.path.split("?", 1)[0]
        if path == "/api/process-now":
            self.handle_process_now()
            return
        if path == "/api/clear-jobs":
            self.handle_clear_jobs()
            return
        if path != "/api/upload":
            self._fail(404, "not found")
            return
        if not self.allow_upload:
            self._fail(403, "uploads are off. Start the server with --allow-upload.")
            return
        name = (self._query().get("name") or "").strip()
        ok, message = self.check_upload_name(name)
        if not ok:
            self._fail(400, message)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            self._fail(411, "send a Content-Length header")
            return
        if self.max_upload_bytes and length > self.max_upload_bytes:
            self._fail(413, "the file is larger than %d bytes" % self.max_upload_bytes)
            return
        incoming = self.config.paths.incoming
        target = os.path.join(incoming, name)
        if os.path.exists(target):
            self._fail(409, "that file is already in incoming")
            return
        if free_bytes(incoming) < length:
            self._fail(507, "not enough free space in incoming")
            return
        # A leading dot keeps the watcher away until the upload is complete.
        tmp = os.path.join(incoming, "." + name + ".part")
        written = 0
        try:
            with open(tmp, "wb") as handle:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            if written != length:
                os.unlink(tmp)
                self._fail(400, "the upload stopped after %d bytes" % written)
                return
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except OSError as exc:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            log.warning("upload failed: %s", exc)
            self._fail(500, "the server could not write the file")
            return
        log.info("upload finished: %s (%d bytes)", name, written)
        body = json.dumps({"ok": True, "name": name, "size": written}).encode("utf-8")
        self._send(200, body, CONTENT_TYPES[".json"])

    def send_download(self, kind: str, rel: str) -> None:
        """Serve one file from the library or the logs folder."""
        if kind not in KINDS:
            self._fail(400, "kind must be one of: " + ", ".join(sorted(KINDS)))
            return
        folder_name, extensions = KINDS[kind]
        root = getattr(self.config.paths, folder_name, "")
        full = safe_under(root, rel) if root else None
        if full is None:
            self._fail(404, "no such file")
            return
        if os.path.splitext(full)[1].lower() not in extensions:
            self._fail(400, "that file type is not offered for download")
            return
        self._send_file(full)

    def log_message(self, fmt, *args) -> None:
        """Send access lines to the app logger instead of stderr."""
        log.debug("%s - %s", self.address_string(), fmt % args)


def make_server(config, host: str, port: int, interval: float = 5.0, recent: int = 80,
                allow_upload: bool = False, max_upload_bytes: int = 0,
                allow_control: bool = False):
    """Build the HTTP server without starting it."""
    handler = type("BoundHandler", (Handler,), {
        "config": config, "interval": interval, "recent": recent,
        "allow_upload": allow_upload, "max_upload_bytes": max_upload_bytes,
        "allow_control": allow_control,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(config, host: str = "127.0.0.1", port: int = 8090, interval: float = 5.0,
          recent: int = 80, allow_upload: bool = False, max_upload_bytes: int = 0,
          allow_control: bool = False) -> int:
    """Run the status server until SIGTERM or SIGINT arrives."""
    try:
        httpd = make_server(config, host, port, interval, recent, allow_upload,
                            max_upload_bytes, allow_control)
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
    if allow_upload:
        limit = "%.0f GiB" % (max_upload_bytes / 1024 ** 3) if max_upload_bytes else "no limit"
        print("uploads are on: %s into %s (max %s)"
              % (", ".join("." + e for e in config.watch.extensions),
                 config.paths.incoming, limit))
    if allow_control:
        print("controls are on: clear the job list, and ask the watcher to start "
              "the queue now")
    try:
        httpd.serve_forever(poll_interval=0.4)
    finally:
        httpd.server_close()
    print("status page stopped")
    return 0
