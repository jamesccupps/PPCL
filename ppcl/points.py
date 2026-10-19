"""The point database: what actually exists in the panel.

Without this, a linter can only check a program against itself. With it, three
things become possible that matter in the field:

* **Unresolved references.** The Desigo CC PPCL Editor marks a line with a red
  ``U`` when it names a point the panel does not have, and that mark is the
  first thing an engineer looks for after a database change. This module
  produces the same finding offline, before the program is loaded.
* **Type-aware checking.** ``ON`` on an LAO is a mistake the panel will accept
  and then ignore. Knowing each point's type turns a whole class of rules on.
* **Real names in completion.** Typing three characters and getting the actual
  point is the difference between a tool you use and a tool you demo.

Exports differ between Desigo CC, Insight and whatever a contractor sends in a
spreadsheet, so column names are matched by alias rather than by position, and
anything unrecognised is reported rather than guessed at.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import asdict, dataclass

from . import spec

#: Column aliases, lowercased and stripped of spaces and underscores. The
#: first match wins, so put the most specific alias first.
COLUMNS = {
    "name": ("pointname", "name", "point", "objectname", "systemname",
             "pointid", "descriptor", "technicaldesignation"),
    "ptype": ("pointtype", "type", "ptype", "objecttype", "pointtypename"),
    "description": ("description", "pointdescriptor", "descriptor", "text",
                    "comment", "userdescription"),
    "units": ("units", "unit", "engineeringunits", "eu"),
    "device": ("device", "panel", "fieldpanel", "controller", "node",
               "deviceid"),
    "address": ("address", "hardwareaddress", "point address", "slot"),
    "value": ("value", "presentvalue", "currentvalue", "lastvalue"),
    "kind": ("kind", "category", "class"),
    "slope": ("slope", "gain", "scalefactor", "conversionslope"),
    "intercept": ("intercept", "offset", "conversionintercept", "bias"),
}


# --------------------------------------------------------------------------
# What a point type is, in the terms an engineer picks it by
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeInfo:
    """One point type, described for a person choosing or reading it.

    ``reads`` is what a program gets when it reads the point. ``role`` is
    whether the program should command it at all. Neither is a column in a
    point export: both follow from the type.
    """

    name: str
    label: str
    family: str  # "APOGEE" | "BACnet"
    #: "analog" | "two-state" | "multistate" | "status" | "counter" |
    #: "controller"
    reads: str
    role: str    # "input" | "output" | "value" | "controller"
    #: The status words a program compares a "status" point against.
    states: tuple = ()


#: What a read gives the program, in one sentence.
READS_HINT = {
    "analog": "Reads as its engineering value, such as 72.5.",
    "two-state": "Reads as 1 when ON and 0 when OFF.",
    "multistate": "Reads as a state number, starting at 1.",
    "status": "Compare it with %s.",
    "counter": "Reads as a running count.",
    "controller": "Compare it with %s.",
}

#: Whether the program should command it.
ROLE_HINT = {
    "input": "An input from the field. Read it; do not command it.",
    "output": "An output the program can command.",
    "value": "A value object, with no hardware behind it.",
    "controller": "A controller the program switches between day and night.",
}

_APOGEE_ROLES = {"LAI": "input", "LDI": "input", "LPACI": "input",
                 "LCTLR": "controller"}
# L2SL and L2SP are bundled -- an output plus a proof input -- but read as
# ON/OFF. The On/Off/Auto and speed types are compared against status words
# rather than used as numbers: how a panel numbers AUTO, FAST and SLOW is not
# published, so nothing here assumes it.
_APOGEE_READS = {"LAI": "analog", "LAO": "analog", "LDI": "two-state",
                 "LDO": "two-state", "L2SL": "two-state", "L2SP": "two-state",
                 "LPACI": "counter", "LCTLR": "controller"}
_STATES = {"LOOAL": ("ON", "OFF", "AUTO"), "LOOAP": ("ON", "OFF", "AUTO"),
           "LCTLR": ("DAYMOD", "NGTMOD")}
_SPEED_STATES = ("FAST", "SLOW", "OFF")

_BACNET_READS = {"A": "analog", "B": "two-state", "M": "multistate"}
_BACNET_ROLES = {"I": "input", "O": "output", "V": "value"}


def _build_types():
    out = {}
    for name, pt in spec.POINT_TYPES.items():
        out[name] = TypeInfo(
            name=name, label=pt.description, family="APOGEE",
            reads=_APOGEE_READS.get(name, "status"),
            role=_APOGEE_ROLES.get(name, "output"),
            states=_STATES.get(
                name, _SPEED_STATES if name in spec.SPEED_TYPES else ()),
        )
    for name, label in spec.BACNET_OBJECT_TYPES.items():
        out[name] = TypeInfo(
            name=name, label=label, family="BACnet",
            reads=_BACNET_READS[name[0]], role=_BACNET_ROLES[name[1]],
        )
    return out


#: Every type a point can have, APOGEE first, keyed by name.
TYPES = _build_types()


#: Every type, labelled and grouped the way an engineer looks for it, for
#: the Point block and the Builder. Each type in ``TYPES`` appears exactly
#: once; a test holds that.
#: On APOGEE a virtual point is an LAO or LDO with no physical address, so
#: those two say so rather than pretending virtual is a type of its own.
TYPE_CHOICES = (
    ("LAI", "LAI — analog input (sensor)", "APOGEE: analog and digital"),
    ("LAO", "LAO — analog output, or virtual analog",
     "APOGEE: analog and digital"),
    ("LDI", "LDI — digital input (status, contact)",
     "APOGEE: analog and digital"),
    ("LDO", "LDO — digital output, or virtual digital",
     "APOGEE: analog and digital"),
    ("L2SL", "L2SL — two-state, latched, with proof", "APOGEE: motors"),
    ("L2SP", "L2SP — two-state, pulsed, with proof", "APOGEE: motors"),
    ("LOOAL", "LOOAL — on/off/auto, latched", "APOGEE: motors"),
    ("LOOAP", "LOOAP — on/off/auto, pulsed", "APOGEE: motors"),
    ("LFSSL", "LFSSL — fast/slow/stop, latched", "APOGEE: motors"),
    ("LFSSP", "LFSSP — fast/slow/stop, pulsed", "APOGEE: motors"),
    ("LFMSSL", "LFMSSL — fast/medium/slow/stop, latched", "APOGEE: motors"),
    ("LFMSSP", "LFMSSP — fast/medium/slow/stop, pulsed", "APOGEE: motors"),
    ("LPACI", "LPACI — pulse accumulator (meter)", "APOGEE: other"),
    ("LCTLR", "LCTLR — controller (day/night)", "APOGEE: other"),
    ("AI", "AI — analog input", "BACnet: analog"),
    ("AO", "AO — analog output", "BACnet: analog"),
    ("AV", "AV — analog value (virtual)", "BACnet: analog"),
    ("BI", "BI — binary input", "BACnet: binary"),
    ("BO", "BO — binary output", "BACnet: binary"),
    ("BV", "BV — binary value (virtual)", "BACnet: binary"),
    ("MI", "MI — multistate input", "BACnet: multistate"),
    ("MO", "MO — multistate output", "BACnet: multistate"),
    ("MV", "MV — multistate value (virtual)", "BACnet: multistate"),
)


def type_info(ptype):
    """The :class:`TypeInfo` for ``ptype``, or None if it is not one."""
    return TYPES.get((ptype or "").strip().upper())


def describe(info):
    """A type in plain language, for the UI."""
    reads = READS_HINT[info.reads]
    if "%s" in reads:
        reads = reads % (", ".join(info.states[:-1]) + " or "
                         + info.states[-1])
    return {"name": info.name, "label": info.label, "family": info.family,
            "reads": info.reads, "role": info.role,
            "reads_hint": reads, "role_hint": ROLE_HINT[info.role]}


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", str(header).strip().lower())


@dataclass
class PointRecord:
    """One point as the panel knows it."""

    name: str
    ptype: str = ""
    description: str = ""
    units: str = ""
    device: str = ""
    address: str = ""
    value: float = None
    #: Engineering conversion. An analog point's engineering value maps to a
    #: digital count through these, and the panel refuses any command whose
    #: count falls outside 0 to 32,767 -- error E12, "Value out of range".
    #: The classic way to hit it is a virtual LAO defined with intercept 0
    #: commanded to a negative value.
    slope: float = None
    intercept: float = None

    @property
    def key(self):
        return self.name.upper()

    @property
    def kind(self):
        """What a read of the point gives, from its type: one of
        :data:`READS_HINT`'s keys, or ``unknown``.

        This used to call every non-analog APOGEE type "digital" -- a pulse
        counter and an On/Off/Auto motor included -- and every BACnet
        multistate object "unknown".
        """
        info = type_info(self.ptype)
        return info.reads if info else "unknown"

    def to_dict(self):
        data = asdict(self)
        data["kind"] = self.kind
        return data


class PointDatabase:
    """A set of points, looked up by name."""

    def __init__(self, points=(), source=""):
        self.points = {}
        self.source = source
        #: Columns in the import that were not recognised, so the user can see
        #: what was dropped rather than wondering.
        self.ignored_columns = []
        self.problems = []
        for p in points:
            self.add(p)

    def __len__(self):
        return len(self.points)

    def __contains__(self, name):
        return self.lookup(name) is not None

    def add(self, record: PointRecord):
        self.points[record.key] = record
        return record

    # -- lookup ------------------------------------------------------------

    def lookup(self, name):
        """Find a point, tolerating the quoting and prefixes PPCL allows.

        A reference may be quoted, may carry a ``$`` local sigil, may be
        colon-qualified for an FLN subpoint, and may use a DEFINE abbreviation
        that has already been expanded. The bare name is tried first, then the
        segment after the last separator, which is what makes a program
        written against ``Bld01.Ahu01.RAF`` resolve in a database exported
        with short names.
        """
        if not name:
            return None
        text = str(name).strip().strip('"')
        if not text:
            return None
        candidates = [text]
        if text.startswith("$"):
            candidates.append(text[1:])
        if ":" in text:
            candidates.append(text.rsplit(":", 1)[-1])
        if "." in text:
            candidates.append(text.rsplit(".", 1)[-1])
        for candidate in candidates:
            hit = self.points.get(candidate.upper())
            if hit is not None:
                return hit
        return None

    def search(self, query, limit=40):
        """Substring search, prefix matches first. Feeds autocomplete."""
        query = str(query or "").strip().upper()
        if not query:
            return list(self.points.values())[:limit]
        prefix, contains = [], []
        for record in self.points.values():
            upper = record.key
            if upper.startswith(query):
                prefix.append(record)
            elif query in upper or query in record.description.upper():
                contains.append(record)
        prefix.sort(key=lambda r: r.key)
        contains.sort(key=lambda r: r.key)
        return (prefix + contains)[:limit]

    def types(self):
        """``{NAME: TYPE}``, the mapping the linter's type rules want."""
        return {
            record.key: record.ptype.upper()
            for record in self.points.values()
            if record.ptype
        }

    def values(self):
        """``{NAME: value}`` for every point that carried one, to seed a run."""
        return {
            record.key: record.value
            for record in self.points.values()
            if record.value is not None
        }

    # -- checking ----------------------------------------------------------

    def unresolved(self, program, analysis=None):
        """Point references the database does not contain.

        This is the offline equivalent of the red ``U`` the PPCL Editor puts
        in the status column. Locals, resident points, status indicators and
        anything that looks like a DEFINE abbreviation are excluded, because
        none of those live in the point database.
        """
        from . import analyzer

        analysis = analysis or analyzer.analyze(program)
        missing = {}
        for use in analysis.uses:
            name = use.name
            bare = str(name).strip().strip('"')
            if not bare or bare.startswith("$"):
                continue
            if "%" in bare:
                continue                      # an unexpanded DEFINE
            upper = bare.upper()
            if spec.is_resident(upper) or upper in spec.RESIDENT_POINTS:
                continue
            if upper in spec.STATUS_INDICATORS or upper in spec.RESERVED_WORDS:
                continue
            if self.lookup(bare) is not None:
                continue
            row = missing.setdefault(upper, {"name": bare, "lines": []})
            if use.line not in row["lines"]:
                row["lines"].append(use.line)
        return sorted(missing.values(), key=lambda r: r["name"])


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def _map_columns(fieldnames):
    """Map a file's headers onto our fields. Returns ``(mapping, ignored)``."""
    normalised = {_norm(f): f for f in fieldnames or []}
    mapping = {}
    used = set()
    for field_name, aliases in COLUMNS.items():
        for alias in aliases:
            key = _norm(alias)
            if key in normalised and normalised[key] not in used:
                mapping[field_name] = normalised[key]
                used.add(normalised[key])
                break
    ignored = [f for f in (fieldnames or []) if f not in used]
    return mapping, ignored


def load_csv(text, source=""):
    """Read a CSV or tab-separated export."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    mapping, ignored = _map_columns(reader.fieldnames)
    db = PointDatabase(source=source)
    db.ignored_columns = ignored
    if "name" not in mapping:
        raise ValueError(
            "no point-name column found. Looked for any of: %s. The file's "
            "columns are: %s"
            % (", ".join(COLUMNS["name"]),
               ", ".join(reader.fieldnames or ["(none)"]))
        )
    for number, row in enumerate(reader, start=2):
        name = (row.get(mapping["name"]) or "").strip()
        if not name:
            continue
        record = PointRecord(name=name)
        for field_name in ("ptype", "description", "units", "device",
                           "address"):
            column = mapping.get(field_name)
            if column:
                setattr(record, field_name,
                        (row.get(column) or "").strip())
        for field_name in ("value", "slope", "intercept"):
            column = mapping.get(field_name)
            if column:
                raw = (row.get(column) or "").strip()
                setattr(record, field_name, _to_value(raw))
        if record.key in db.points:
            db.problems.append(
                "line %d: %s appears more than once; the last one wins"
                % (number, name)
            )
        db.add(record)
    return db


def _to_value(raw):
    if raw == "":
        return None
    named = {"ON": 1.0, "OFF": 0.0, "TRUE": 1.0, "FALSE": 0.0,
             "AUTO": 1.0, "OPEN": 1.0, "CLOSED": 0.0}
    if raw.upper() in named:
        return named[raw.upper()]
    try:
        return float(raw)
    except ValueError:
        return None


def load_json(text, source=""):
    """Read a JSON export: a list of objects, or ``{"points": [...]}``."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("points", data.get("Points", []))
    if not isinstance(data, list):
        raise ValueError(
            "expected a list of point objects, or an object with a 'points' key"
        )
    db = PointDatabase(source=source)
    for raw in data:
        if not isinstance(raw, dict):
            continue
        mapping, _ = _map_columns(list(raw))
        if "name" not in mapping:
            continue
        record = PointRecord(name=str(raw[mapping["name"]]).strip())
        if not record.name:
            continue
        for field_name in ("ptype", "description", "units", "device",
                           "address"):
            column = mapping.get(field_name)
            if column and raw.get(column) is not None:
                setattr(record, field_name, str(raw[column]).strip())
        column = mapping.get("value")
        if column and raw.get(column) is not None:
            record.value = _to_value(str(raw[column]).strip())
        db.add(record)
    return db


def load(text, source=""):
    """Read either format, deciding from the content rather than the name."""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return load_json(text, source=source)
    return load_csv(text, source=source)


def load_file(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return load(fh.read(), source=os.path.basename(path))


def dumps(db: PointDatabase) -> str:
    return json.dumps(
        {"source": db.source,
         "points": [p.to_dict() for p in db.points.values()]},
        indent=2,
    )
