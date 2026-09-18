"""Anonymise site-identifying point names so a program can be shared.

Real PPCL is full of building names, panel names, floor and tenant identifiers.
This rewrites those while leaving the control logic byte-identical, so a program
can go into a ticket, a vendor email, or a public repository without carrying
the site's topology with it.

The mapping is written out separately and stays local, so the redaction is
reversible on your own machine and nowhere else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import spec

#: Generic HVAC and controls vocabulary. These segments say what a point does,
#: not where it is, so they survive redaction and keep the program readable.
GENERIC_SEGMENTS = frozenset(
    """
    AHU RTU VAV FCU CUH UH EF SF RF MAU DOAS ERV HRV
    SFAN RFAN EFAN MFAN FAN BLOWER
    OAT OAH RAT MAT DAT SAT SPT ZNT RMT CHWS CHWR HWS HWR CWS CWR
    SP SPT SETPT SETPOINT DASP DAPSP SASP HWSP CHWSP STPT
    TEMP TMP HUM RH CO2 PRESS PRESSURE STATIC DP FLOW CFM GPM
    VLV VALVE DPR DAMPER ACT ACTUATOR HVLV CVLV OADPR RADPR EADPR
    PMP PUMP CHLR CHILLER BLR BOILER CT COMP COMPRESSOR
    ALM ALARM STS STATUS CMD COMMAND ENA ENABLE FB FEEDBACK PROOF
    OCC UNOCC OCCUPIED UNOCCUPIED SCHED SCHEDULE
    KW KWH BTU MBTU TONS LOAD DEMAND
    HTG CLG HEAT COOL ECON ECONOMIZER FREEZE FRZ FRZSTAT SMOKE
    MIN MAX AVG TOTAL HIGH LOW
    """.split()
)


@dataclass
class Redaction:
    """The result of redacting a set of programs."""

    mapping: dict = field(default_factory=dict)  # original -> replacement
    files: dict = field(default_factory=dict)  # path -> redacted text
    #: Generic terms left in place, for the caller to eyeball.
    preserved: set = field(default_factory=set)

    def reverse(self) -> dict:
        return {v: k for k, v in self.mapping.items()}

    def save_mapping(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"note": "Keep this file local. It reverses the redaction.",
                 "mapping": self.mapping},
                fh,
                indent=2,
                sort_keys=True,
            )


class Redactor:
    """Rewrites point names segment by segment, consistently across files."""

    def __init__(self, keep_generic: bool = True, prefix: str = "PT"):
        self.keep_generic = keep_generic
        self.prefix = prefix
        self.mapping = {}
        self._counters = {}
        #: Generic segments that were preserved. The caller should show these
        #: to the user: the whitelist is a heuristic, and a site abbreviation
        #: can collide with it: OCC is "occupied" here and is also a common
        #: building abbreviation, and CT is a cooling tower and also a state.
        self.preserved = set()

    def _token(self, kind: str) -> str:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return "%s%03d" % (kind, self._counters[kind])

    def segment(self, seg: str) -> str:
        """Redact one dot-separated segment of a point name."""
        if not seg:
            return seg
        upper = seg.upper()

        # Never touch language keywords or system points.
        if upper in spec.RESERVED_WORDS or spec.is_resident(upper):
            return seg
        if self.keep_generic and upper in GENERIC_SEGMENTS:
            self.preserved.add(upper)
            return seg
        # A segment that is a generic word plus a number (AHU01, VAV3-12) keeps
        # its word and has the number normalised, which preserves readability
        # without revealing how many units a site actually has.
        m = re.match(r"^([A-Z]+)[-_]?(\d+)$", upper)
        if self.keep_generic and m and m.group(1) in GENERIC_SEGMENTS:
            self.preserved.add(m.group(1))
            key = upper
            if key not in self.mapping:
                self.mapping[key] = "%s%02d" % (
                    m.group(1),
                    self._bump("unit:" + m.group(1)),
                )
            return self.mapping[key]

        if upper not in self.mapping:
            self.mapping[upper] = self._token(self.prefix)
        return self.mapping[upper]

    def _bump(self, key: str) -> int:
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def name(self, name: str) -> str:
        """Redact a full, possibly dotted, point name."""
        sigil = ""
        body = name
        if body.startswith("$"):
            sigil, body = "$", body[1:]
        elif body.startswith("@"):
            sigil, body = "@", body[1:]
        redacted = ".".join(self.segment(part) for part in body.split("."))
        return sigil + redacted

    # -- text rewriting ----------------------------------------------------

    _QUOTED = re.compile(r'"([^"]*)"')
    _BARE = re.compile(r"(?<![\w.$@\"])([A-Za-z_$][A-Za-z0-9_$]*)")

    def redact_text(self, text: str) -> str:
        """Redact a whole PPCL source file.

        Comment bodies are dropped rather than rewritten: free text is where
        tenant names, room numbers and contact details actually live, and no
        token substitution is going to catch them reliably.
        """
        from .parser import split_line_number, _is_comment_body

        out = []
        for raw in text.splitlines():
            number, body = split_line_number(raw)
            if number is None:
                out.append(self._redact_statement(raw))
                continue
            stripped = body.strip()
            if _is_comment_body(stripped):
                out.append("%s\tC %s" % (str(number).zfill(5), "[redacted]")
                           if stripped[1:].strip() else "%s\tC" % str(number).zfill(5))
                continue
            out.append("%s\t%s" % (str(number).zfill(5),
                                   self._redact_statement(body)))
        return "\n".join(out) + "\n"

    def _redact_statement(self, body: str) -> str:
        def quoted(m):
            return '"%s"' % self.name(m.group(1))

        body = self._QUOTED.sub(quoted, body)

        def bare(m):
            word = m.group(1)
            upper = word.upper()
            if upper in spec.RESERVED_WORDS or spec.is_resident(upper):
                return word
            if spec.is_builtin_local(word):
                return word
            return self.name(word)

        return self._BARE.sub(bare, body)


def redact_files(paths, keep_generic: bool = True) -> Redaction:
    """Redact a set of files with one shared, consistent mapping."""
    r = Redactor(keep_generic=keep_generic)
    result = Redaction()
    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            result.files[path] = r.redact_text(fh.read())
    result.mapping = dict(r.mapping)
    result.preserved = set(r.preserved)
    return result
