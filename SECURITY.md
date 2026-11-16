# Security policy

## Reporting a vulnerability

Please report it privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. Please don't open a
public issue for it.

Include what you ran, what happened, and the smallest input that shows it.

## Supported versions

Fixes go into the latest release and `main`. Older releases are not patched.

## What counts

PPCL Workbench reads and writes files on the engineer's own machine. It has
no network client and never talks to a building system: no panel writes, no
BACnet or P2 connection. The parts with a security boundary are:

- **The local web server** (`ppcl serve`). It binds 127.0.0.1 by default,
  checks the `Host` header, accepts only same-origin JSON, and confines every
  path to the workspace folder, refusing hidden files and folders and any
  file type it does not handle. Anything that lets another web page, or
  anything outside that folder, read or write a file, or run code, is in
  scope.
- **The MCP server.** It exposes analysis only and has no file-writing
  tools. A way to make it write a file is in scope.
- **Redaction** (`ppcl redact`). A site name that survives into the output
  is in scope; the command is meant to refuse to write when it finds one.

Out of scope: starting the server with `--host` on a network you don't
trust. That bind has no authentication, says so when it starts, and the
README says the same.
