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
from dataclasses import asdict, dataclass, field

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
        """``analog``, ``digital`` or ``unknown``, from the point type."""
        upper = (self.ptype or "").upper()
        if upper in spec.ANALOG_TYPES:
            return "analog"
        if upper in spec.POINT_TYPES:
            return "digital"
        if upper in ("AI", "AO", "AV"):
            return "analog"
        if upper in ("BI", "BO", "BV"):
            return "digital"
        return "unknown"

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
