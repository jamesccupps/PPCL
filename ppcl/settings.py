"""Workbench settings, stored as JSON next to the workspace.

Deliberately small. A settings file that can express anything is a settings
file nobody can debug, so every key is declared here with a type, a default,
a help string and, where it matters, a range. Unknown keys are kept rather
than dropped -- a newer version of the workbench may have written them -- but
they are reported so a typo does not silently do nothing.

Nothing here changes what the language means. Firmware is the one setting that
changes a result, and it is surfaced in every diagnostic that depends on it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import spec

FILENAME = ".ppcl-workbench.json"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str            # bool | int | float | choice | text | list
    default: object
    doc: str = ""
    choices: tuple = ()
    minimum: float = None
    maximum: float = None
    group: str = "General"


SETTINGS = [
    # -- language ----------------------------------------------------------
    Setting("firmware", "Field panel firmware", "choice", "apogee",
            "Changes real results: the operand limit is 16 on APOGEE and 13 "
            "on older firmware, and a GOTO to a missing line is an error on "
            "APOGEE but a silent redirect on older panels.",
            choices=tuple(f.value for f in spec.Firmware), group="Language"),
    Setting("disabled_rules", "Disabled rules", "list", [],
            "Rule codes to suppress, such as S601. Prefer fixing the code; a "
            "rule you always ignore is worth arguing about instead.",
            group="Language"),

    # -- editor ------------------------------------------------------------
    Setting("line_start", "First line number", "int", 10,
            "Where renumbering starts.", minimum=1, maximum=32767,
            group="Editor"),
    Setting("line_step", "Line increment", "int", 10,
            "Gap left between statements. Ten leaves room to insert without "
            "renumbering, which is the manual's own advice.",
            minimum=1, maximum=1000, group="Editor"),
    Setting("quick_numbering", "Quick numbering", "bool", True,
            "Number the next line automatically when you press Enter at the "
            "end of a line, the way the Desigo CC editor does.",
            group="Editor"),
    Setting("tab_width", "Tab width", "int", 8,
            "Panels use a tab between the line number and the statement.",
            minimum=1, maximum=16, group="Editor"),
    Setting("font_size", "Editor font size", "int", 13,
            "Pixels. Ctrl and the mouse wheel changes this too.",
            minimum=9, maximum=28, group="Editor"),
    Setting("autocomplete", "Command Assist", "bool", True,
            "Suggest commands, points and priorities as you type.",
            group="Editor"),
    Setting("hover_help", "Hover help", "bool", True,
            "Show a command's signature and notes when the pointer rests on "
            "it.", group="Editor"),
    Setting("lint_delay", "Lint delay (ms)", "int", 220,
            "How long to wait after the last keystroke before checking.",
            minimum=0, maximum=3000, group="Editor"),
    Setting("wrap_long_lines", "Warn on long lines", "bool", True,
            "Flag statements over the MMI character limit, which load from a "
            "workstation but cannot be typed at the panel.",
            group="Editor"),

    # -- bench -------------------------------------------------------------
    Setting("bench_hours", "Default run length (hours)", "float", 3.0,
            "", minimum=0.1, maximum=24.0, group="Bench"),
    Setting("bench_start_hour", "Default start hour", "float", 5.0,
            "Simulated time of day the run begins.",
            minimum=0.0, maximum=23.9, group="Bench"),
    Setting("bench_weather", "Default weather", "text", "design_winter",
            "", group="Bench"),
    Setting("bench_preset", "Default plant", "text", "single_zone_ahu",
            "", group="Bench"),
    Setting("bench_dt", "Timestep (seconds)", "float", 10.0,
            "Smaller resolves fast dynamics; larger runs quicker. Actuator "
            "stroke times are tens of seconds, so ten is usually enough.",
            minimum=1.0, maximum=60.0, group="Bench"),

    # -- files -------------------------------------------------------------
    Setting("backup_on_save", "Keep a .bak on save", "bool", True,
            "Writes the previous contents alongside the file before "
            "overwriting it.", group="Files"),
    Setting("point_database", "Point database file", "text", "",
            "A CSV or JSON export from Desigo CC or Insight, relative to the "
            "workspace. Turns on unresolved-point marking and type-aware "
            "rules.", group="Files"),

    # -- appearance --------------------------------------------------------
    Setting("theme", "Theme", "choice", "dark", "",
            choices=("dark", "light"), group="Appearance"),
    Setting("show_tooltips", "Show tooltips", "bool", True,
            "Explain each control when the pointer rests on it.",
            group="Appearance"),
]

BY_KEY = {s.key: s for s in SETTINGS}


def defaults() -> dict:
    return {s.key: (list(s.default) if isinstance(s.default, list)
                    else s.default)
            for s in SETTINGS}


def coerce(key, value):
    """Coerce and range-check one setting. Raises ValueError with a reason."""
    setting = BY_KEY.get(key)
    if setting is None:
        return value  # an unknown key is preserved, not validated
    if setting.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if setting.kind in ("int", "float"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("%s must be a number, not %r" % (setting.label,
                                                              value))
        if setting.minimum is not None and number < setting.minimum:
            raise ValueError(
                "%s must be at least %g" % (setting.label, setting.minimum)
            )
        if setting.maximum is not None and number > setting.maximum:
            raise ValueError(
                "%s must be at most %g" % (setting.label, setting.maximum)
            )
        return int(number) if setting.kind == "int" else number
    if setting.kind == "choice":
        text = str(value)
        if setting.choices and text not in setting.choices:
            raise ValueError(
                "%s must be one of %s"
                % (setting.label, ", ".join(setting.choices))
            )
        return text
    if setting.kind == "list":
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [str(v).strip() for v in (value or []) if str(v).strip()]
    return str(value)


class Settings:
    """A settings file, loaded leniently and saved strictly."""

    def __init__(self, root="."):
        self.root = os.path.abspath(root)
        self.path = os.path.join(self.root, FILENAME)
        self.values = defaults()
        self.unknown_keys = []
        self.problems = []

    def load(self):
        """Read the file if it exists. A broken file is reported, not fatal."""
        self.problems = []
        self.unknown_keys = []
        if not os.path.isfile(self.path):
            return self
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self.problems.append(
                "%s could not be read (%s), so defaults are in use."
                % (FILENAME, exc)
            )
            return self
        if not isinstance(data, dict):
            self.problems.append(
                "%s does not contain a JSON object, so defaults are in use."
                % FILENAME
            )
            return self
        for key, value in data.items():
            if key not in BY_KEY:
                self.unknown_keys.append(key)
                self.values[key] = value
                continue
            try:
                self.values[key] = coerce(key, value)
            except ValueError as exc:
                self.problems.append("%s (using the default)" % exc)
        if self.unknown_keys:
            self.problems.append(
                "settings not recognised, and left untouched: %s"
                % ", ".join(sorted(self.unknown_keys))
            )
        return self

    def save(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.values, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return self

    def update(self, changes: dict):
        """Apply changes, validating each. Returns the list of rejections."""
        rejected = []
        for key, value in (changes or {}).items():
            if key not in BY_KEY:
                rejected.append("%s is not a setting" % key)
                continue
            try:
                self.values[key] = coerce(key, value)
            except ValueError as exc:
                rejected.append(str(exc))
        return rejected

    def get(self, key, default=None):
        return self.values.get(key, default)

    def firmware(self):
        try:
            return spec.Firmware(self.values.get("firmware", "apogee"))
        except ValueError:
            return spec.Firmware.APOGEE

    def schema(self):
        """The declaration, for rendering the settings form."""
        return [
            {
                "key": s.key, "label": s.label, "kind": s.kind,
                "default": s.default, "doc": s.doc,
                "choices": list(s.choices), "min": s.minimum,
                "max": s.maximum, "group": s.group,
                "value": self.values.get(s.key, s.default),
            }
            for s in SETTINGS
        ]

    def groups(self):
        out = []
        for s in SETTINGS:
            if s.group not in out:
                out.append(s.group)
        return out
