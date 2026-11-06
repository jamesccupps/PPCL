"""Reading and writing program files without changing a byte nobody edited.

Desigo and Insight export ASCII with CRLF line endings, and a comment can
carry a Windows-1252 byte -- a degree sign is 0xB0 -- that is not valid UTF-8.
Some editors add a UTF-8 byte-order mark. A tool that reads such a file as
UTF-8 with replacement, and writes it back as UTF-8 with LF, changes every line
of it and destroys the degree sign; one that writes the backup the same way
destroys the only other copy. This module is the one place files are decoded
and encoded, so the web IDE and the command line cannot disagree about it.
"""

from __future__ import annotations

import codecs
import os
import shutil
import tempfile
from dataclasses import dataclass

#: How a file is read, in order. ``utf-8-sig`` is UTF-8 with a byte-order
#: mark; Latin-1 is last because it decodes any byte at all. A file is
#: written back in the encoding it was read in.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass
class TextFile:
    """A file's text, with LF line endings, and how to write it back."""

    text: str
    encoding: str = "utf-8"
    newline: str = "\n"


def decode(raw: bytes) -> TextFile:
    """Decode bytes the way a program file is read."""
    if raw.startswith(codecs.BOM_UTF8):
        text, encoding = raw.decode("utf-8-sig"), "utf-8-sig"
    else:
        for encoding in ENCODINGS[1:]:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    # The style most lines use, so a file written back keeps it.
    crlf = raw.count(b"\r\n")
    newline = "\r\n" if crlf and crlf >= raw.count(b"\n") - crlf else "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextFile(text=text, encoding=encoding, newline=newline)


def read(path: str) -> TextFile:
    with open(path, "rb") as fh:
        return decode(fh.read())


def encode(text: str, encoding: str = "utf-8", newline: str = "\n") -> bytes:
    """Encode ``text`` the way the file it came from was written.

    Raises ValueError naming the line of the first character the encoding
    cannot hold, rather than substituting something for it.
    """
    if newline != "\n":
        text = text.replace("\n", newline)
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as err:
        line = text.count("\n", 0, err.start) + 1
        raise ValueError(
            "line %d contains %r, which %s cannot hold. The file was read in "
            "that encoding and is written the same way so nothing else in it "
            "changes; remove the character to save."
            % (line, text[err.start], encoding)
        ) from None


def write(path: str, data: bytes, backup: bool = False) -> None:
    """Replace ``path`` with ``data`` so a failure leaves the original intact.

    The backup is a byte copy of the file as it was, never a round trip
    through a decoder. The new content goes to a temporary file beside the
    original first and is moved over it in one step.
    """
    if backup and os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    folder = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=".ppcl-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if os.path.exists(path):
            # A temporary file is created private (0600 on POSIX); the file
            # it replaces keeps the permissions it had.
            shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise
