"""A local HTTP shell over :mod:`ppcl.web.api`.

Standard library only, single file, no framework. It exists to host the UI on
the engineer's own machine, not to be a web service.

Deliberate limits, because this process writes files:

* Binds **127.0.0.1** by default. Binding anywhere else requires an explicit
  ``--host`` and prints a warning, because the API can write into the
  workspace and has no authentication.
* Every file path is resolved through :class:`~ppcl.web.api.Workspace`, which
  rejects absolute paths and anything escaping the root.
* Request bodies are capped, and a handler that raises returns a 500 rather
  than killing the server.
* No shell execution, no outbound connections, no building-system access.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .api import MAX_UPLOAD, Workspace, dispatch

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

#: Content types stated outright rather than asked of the platform. On Windows
#: ``mimetypes`` reads the registry, where ``.js`` is regularly mapped to
#: ``text/plain`` by whatever was installed last -- and a module script served
#: as text/plain is refused by the browser, so the whole UI silently fails to
#: start. Being explicit costs four lines and removes a class of support call.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    """Serves the UI and the JSON API."""

    server_version = "PPCLWorkbench"
    sys_version = ""

    workspace = None
    quiet = False

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, status, payload, content_type="application/json"):
        if isinstance(payload, (dict, list)):
            data = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # This UI loads nothing from anywhere else, so say so.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass  # the browser navigated away mid-response

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/meta":
            status, payload = dispatch("/api/meta", {}, self.workspace)
            return self._send(status, payload)
        if path == "/api/files":
            status, payload = dispatch("/api/files", {}, self.workspace)
            return self._send(status, payload)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad Content-Length"})
        if length > MAX_UPLOAD:
            return self._send(413, {"error": "request too large"})
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": "invalid JSON: %s" % exc})
        if not isinstance(body, dict):
            return self._send(400, {"error": "the request body must be an object"})
        status, payload = dispatch(path, body, self.workspace)
        return self._send(status, payload)

    # -- static files ------------------------------------------------------

    def _static(self, relative):
        safe = os.path.normpath(relative).replace("\\", "/")
        if safe.startswith("..") or os.path.isabs(safe):
            return self._send(403, {"error": "forbidden"})
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext)
        if ctype is None:
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            return self._send(200, fh.read(), ctype)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded so a long bench run does not block the editor's lint calls.

    ``allow_reuse_address`` is off on Windows deliberately. There SO_REUSEADDR
    does not mean "reuse a socket in TIME_WAIT", it means "bind even though
    someone else already has this port" -- so a second workbench started while
    the first is still running binds successfully and the two split requests
    between them at random. The symptom is an endpoint that answers 404 on one
    reload and 200 on the next, which costs an hour to diagnose. Failing to
    start is the better outcome, and :func:`serve` says what to do about it.
    """

    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        HTTPServer.server_bind(self)


def serve(host="127.0.0.1", port=8765, workspace=".", open_browser=True,
          quiet=False):
    """Start the workbench UI. Blocks until interrupted."""
    ws = Workspace(workspace)
    if not os.path.isdir(ws.root):
        raise SystemExit("workspace directory does not exist: %s" % ws.root)

    Handler.workspace = ws
    Handler.quiet = quiet
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise SystemExit(
            "cannot bind %s:%d (%s).\n"
            "A workbench is probably already running there -- open "
            "http://localhost:%d/ instead, or pass --port for a second one."
            % (host, port, exc, port)
        )

    url = "http://%s:%d/" % ("localhost" if host == "127.0.0.1" else host, port)
    print("PPCL Workbench")
    print("  serving   %s" % url)
    print("  workspace %s" % ws.root)
    if host not in ("127.0.0.1", "localhost"):
        print()
        print("  WARNING: bound to %s, not localhost. This server has no" % host)
        print("  authentication and can write files inside the workspace.")
        print("  Anyone who can reach this port can edit those files.")
    print()
    print("  Ctrl+C to stop")

    if open_browser:
        def _open():
            import webbrowser

            webbrowser.open(url)

        threading.Timer(0.4, _open).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
