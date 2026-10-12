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
* **Other web pages cannot drive it.** Binding to localhost is not, on its
  own, a boundary: any page open in the same browser can send a POST to
  127.0.0.1, and a "simple" one -- text/plain, no preflight -- goes straight
  through. A page that rebinds its own domain to 127.0.0.1 gets further still,
  and can read the replies. So every request must name this server in its
  ``Host``, and every POST must be ``application/json`` and, when a browser
  sent an ``Origin``, come from this server's own. See :meth:`Handler._refuse`.
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

    # -- request validation ------------------------------------------------

    _LOOPBACK = ("127.0.0.1", "::1", "localhost")

    def _own_hosts(self):
        """Every spelling of this server's address a browser might send."""
        host, port = self.server.server_address[:2]
        names = {"127.0.0.1", "localhost", "[::1]"}
        if host not in self._LOOPBACK:
            names.add("[%s]" % host if ":" in host else host)
        return {"%s:%d" % (name, port) for name in names}

    def _refuse(self, method):
        """Why this request must not be served, or None if it may be.

        Reproduced before this existed: from a page on another domain, a
        text/plain POST to /api/save overwrote a program in the workspace and
        came back 200; with the Host header of a rebound domain, /api/files
        listed every program and the absolute workspace path. The programs in
        that workspace are what gets loaded onto building controllers.

        Three checks, each closing a different door:

        ``Host`` must name this server. A DNS-rebinding page reaches us under
        its own domain name, and this is the only place that shows. Only
        enforced on a loopback bind: someone who deliberately serves on a LAN
        address may reach it under names we cannot list, and has already been
        warned that the port is open and unauthenticated.

        A POST must be ``application/json``. A browser will only send that
        cross-origin after a CORS preflight, and this server answers no
        preflight, so the browser never sends the request at all.

        A POST that carries an ``Origin`` must carry ours. Browsers attach one
        to every POST, so this refuses another page outright. A request with
        no ``Origin`` is not from a browser page -- a script, curl, a test --
        and those were never the threat.
        """
        bound = self.server.server_address[0]
        if bound in self._LOOPBACK:
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in self._own_hosts():
                return (403, "refused: Host %r is not this workbench. Open it "
                        "at http://localhost:%d/ instead."
                        % (host, self.server.server_address[1]))
        if method != "POST":
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";")[0]
        if ctype.strip().lower() != "application/json":
            return (415, "refused: the API only accepts application/json")
        origin = self.headers.get("Origin")
        if origin is not None:
            # Same-origin means the page's origin is the address the request
            # arrived at. On loopback the Host check above has already pinned
            # that to this server; on a deliberate LAN bind it is whatever name
            # the engineer typed, which is why this compares rather than lists.
            here = "http://" + (self.headers.get("Host") or "").strip().lower()
            if origin.strip().lower() != here:
                return (403, "refused: a request from %s cannot use this "
                        "workbench" % origin)
        return None

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
        # frame-ancestors: no other page may frame the UI and steer a click
        # onto Save.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass  # the browser navigated away mid-response

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        refusal = self._refuse("GET")
        if refusal:
            return self._send(refusal[0], {"error": refusal[1]})
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
        # Refused only after the body is off the socket. Answering first and
        # closing with unread bytes queued makes Windows send a TCP reset, and
        # the client sees a dropped connection instead of the reason.
        refusal = self._refuse("POST")
        if refusal:
            return self._send(refusal[0], {"error": refusal[1]})
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
