"""Start the PPCL MCP server with this folder's ``ppcl`` and nothing else.

``.mcp.json`` used to run ``python -m ppcl.mcp_server`` with PYTHONPATH set
to the plugin. Under ``-m`` the current directory goes on sys.path *ahead*
of PYTHONPATH, and when Claude Code starts a plugin's server the current
directory is whatever project it was opened in. A ``ppcl`` folder there ran
instead of the plugin, and a ``json.py`` there replaced the standard
library's.

Now ``.mcp.json`` runs Python with ``-E -s`` (no PYTHON* variables, no user
site-packages -- the toolkit is stdlib-only), drops the current directory
from sys.path before importing anything, and runs this file, which puts its
own folder in that place.
"""

import os
import sys

sys.path[0:0] = [os.path.dirname(os.path.abspath(__file__))]

# -E also drops PYTHONIOENCODING. The protocol is UTF-8 JSON, and a Windows
# console code page must not decide how a degree sign in a comment is sent.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8")

from ppcl.mcp_server import serve  # noqa: E402

if __name__ == "__main__":
    sys.exit(serve())
