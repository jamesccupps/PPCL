"""Diagnostic records produced by the linter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    """How much a finding matters.

    ERROR   -- the panel will reject this, or it provably misbehaves.
    WARNING -- legal PPCL that violates a documented rule or will misbehave
               under conditions the author probably did not intend.
    INFO    -- worth knowing; usually an optimisation opportunity.
    STYLE   -- a guideline from the manual's "PPCL guidelines" section.
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    STYLE = "style"

    @property
    def rank(self) -> int:
        return {"error": 0, "warning": 1, "info": 2, "style": 3}[self.value]


@dataclass
class Diagnostic:
    """One finding against a program."""

    code: str
    severity: Severity
    message: str
    line: int = None  # PPCL line number
    source_line: int = None  # 1-based line in the source file
    detail: str = ""
    manual: str = ""  # citation into the PPCL User's Manual
    suggestion: str = ""
    program: str = ""

    def sort_key(self):
        return (self.severity.rank, self.line if self.line is not None else -1, self.code)

    def format(self, path: str = "", color: bool = False) -> str:
        """Render as a single console line plus optional indented detail."""
        loc = path or self.program or "<program>"
        if self.line is not None:
            loc += ":%d" % self.line
        sev = self.severity.value
        if color:
            hue = {
                "error": "\033[31m",
                "warning": "\033[33m",
                "info": "\033[36m",
                "style": "\033[90m",
            }[sev]
            sev = "%s%s\033[0m" % (hue, sev)
        head = "%s: %s [%s] %s" % (loc, sev, self.code, self.message)
        parts = [head]
        if self.detail:
            parts.append("    " + self.detail)
        if self.suggestion:
            parts.append("    fix: " + self.suggestion)
        if self.manual:
            parts.append("    manual: " + self.manual)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "line": self.line,
            "source_line": self.source_line,
            "detail": self.detail,
            "manual": self.manual,
            "suggestion": self.suggestion,
            "program": self.program,
        }
