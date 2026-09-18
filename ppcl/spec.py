"""Machine-readable PPCL language specification.

Encoded from the APOGEE Powers Process Control Language (PPCL) User's Manual,
125-1896, Rev. 5 (10/00), Siemens Building Technologies.

Every constraint in this module traces to a documented statement in that
manual. Where the manual gives different syntax per firmware family, the
variant is recorded explicitly rather than flattened, because a program that is
legal on APOGEE firmware may be rejected by physical firmware and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# --------------------------------------------------------------------------
# Firmware families
# --------------------------------------------------------------------------


class Firmware(str, Enum):
    """The firmware families the manuals track in their compatibility bars.

    The first five are the 2000 manual's. ``PXC_A`` is the current generation
    (PXC4.E16.A, PXC5.E24.A, PXC7.E400.A) and comes from A6V10374898, whose
    every command page carries its own five-column bar. It matters because
    that generation **dropped** commands the older panels had -- ``OIP`` is
    stated outright as invalid there -- so "does this program run on my panel"
    is a different question from "is this valid PPCL".
    """

    PHYSICAL = "physical"
    LOGICAL = "logical"
    UNITARY = "unitary"
    CM = "cm"
    APOGEE = "apogee"
    PXC_A = "pxc_a"


#: Max characters per program line when entering code through the MMI port.
#: Manual, Chapter 2 "PPCL rules".
MMI_LINE_LIMIT = {
    Firmware.APOGEE: 66,  # including the line number
    # A PXC.A is loaded over BACnet/IP or the web UI rather than typed at an
    # MMI port, and its limit is far larger: A6V10374898 Ch.1 states 512
    # characters including the line number, both for the web user interface
    # and as the general rule. The panel also publishes its own answer as
    # BACnet property 5165 (@MaxPPCLChs), so GETVAL can confirm it on site.
    # The manual adds the obvious caveat: using the full 512 makes a line
    # unreadable, so this is a ceiling and not a target.
    Firmware.PXC_A: 512,
    Firmware.PHYSICAL: 72,
    Firmware.LOGICAL: 72,
    Firmware.UNITARY: 72,
    Firmware.CM: 72,
}

#: Max total characters across continuation (&) lines.
MMI_CONTINUATION_LIMIT = {
    Firmware.APOGEE: 198,  # across a total of three lines
    Firmware.PXC_A: 512,
    Firmware.PHYSICAL: 144,  # across two lines
    Firmware.LOGICAL: 144,
    Firmware.UNITARY: 144,
    Firmware.CM: 144,
}

#: Valid PPCL line numbers, inclusive. Manual, Chapter 2 "PPCL rules".
LINE_MIN = 1
LINE_MAX = 32767

#: Valid length for an APOGEE PPCL *program* name.
PROGRAM_NAME_MAX = 30

#: A point name may appear unquoted only if it is <= 6 characters and uses
#: nothing but A-Z and 0-9. Manual, Chapter 2 "PPCL rules" (APOGEE section).
UNQUOTED_NAME_MAX = 6


# --------------------------------------------------------------------------
# Point types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PointType:
    """One of the point types recognised by every APOGEE field panel.

    ``addresses`` documents the physical sub-addresses the type occupies, from
    Table 3-2 of the manual. This matters because commanding two digital
    outputs of a bundled point simultaneously can damage equipment.
    """

    name: str
    kind: str  # "analog" | "digital" | "bundled" | "counter" | "controller"
    addresses: tuple
    description: str
    #: Whether the proof DI is optional. Documented as optional on every
    #: bundled type EXCEPT L2SL, whose definition states the proof without
    #: qualification. Matters because PRFON on a point with no proof wired can
    #: never be true, and nothing in the program says so.
    proof_optional: bool = False
    notes: tuple = ()


POINT_TYPES = {
    p.name: p
    for p in [
        PointType("LAI", "analog", ("AI",), "Logical analog input"),
        PointType("LAO", "analog", ("AO",), "Logical analog output"),
        PointType("LDI", "digital", ("DI",), "Logical digital input"),
        PointType("LDO", "digital", ("DO",), "Logical digital output"),
        PointType(
            "LFSSL",
            "bundled",
            ("DO(OFF/FAST)", "DO(OFF/SLOW)", "DI(PROOF)"),
            "Fast/Slow/Stop, latched",
            proof_optional=True,
            notes=("Commands two LATCHED digital outputs, Fast/Stop and "
                   "Slow/Stop, and reads one optional latched proof DI.",),
        ),
        PointType(
            "LFSSP",
            "bundled",
            ("DO(OFF)", "DO(FAST)", "DO(SLOW)", "DI(PROOF)"),
            "Fast/Slow/Stop, pulsed",
            proof_optional=True,
            notes=("Commands THREE pulsed digital outputs -- Fast, Slow and "
                   "Stop -- and reads one optional latched proof DI.",),
        ),
        PointType(
            "LOOAL",
            "bundled",
            ("DO(ON/OFF)", "DO(AUTO)", "DI(PROOF)"),
            "On/Off/Auto, latched",
            proof_optional=True,
            notes=("Commands two LATCHED digital outputs, On/Off and Auto, "
                   "and reads one optional latched proof DI.",),
        ),
        PointType(
            "LOOAP",
            "bundled",
            ("DO(ON)", "DO(OFF)", "DO(AUTO)", "DI(PROOF)"),
            "On/Off/Auto, pulsed",
            proof_optional=True,
            notes=("MIXED: On and Off are PULSED digital outputs, Auto is a "
                   "LATCHED one. Reads one optional latched proof DI.",),
        ),
        PointType(
            "LPACI", "counter", ("DI(COUNT)",), "Pulsed accumulator counter input"
        ),
        PointType(
            "L2SL", "bundled", ("DO(ON/OFF)", "DI(PROOF)"),
            "Two-state, latched",
            notes=("Commands one latched digital output and reads one latched "
                   "proof DI. The only bundled type whose proof is documented "
                   "WITHOUT being called optional.",),
        ),
        PointType(
            "L2SP", "bundled", ("DO(ON)", "DO(OFF)", "DI(PROOF)"),
            "Two-state, pulsed",
            proof_optional=True,
            notes=("Commands two PULSED digital outputs, On and Off, and "
                   "reads one optional latched proof DI.",),
        ),
        # Not in Table 3-2 but referenced by the DAY/NIGHT commands.
        PointType("LCTLR", "controller", ("CTLR",), "Logical controller (DAY/NIGHT)"),
    ]
}

#: Point types that carry an analog value.
ANALOG_TYPES = frozenset({"LAI", "LAO"})

#: Point types that accept ON/OFF.
DIGITAL_COMMANDABLE = frozenset(
    {"LDI", "LDO", "L2SL", "L2SP", "LOOAL", "LOOAP", "LFSSL", "LFSSP"}
)

#: Point types that accept AUTO.
AUTO_TYPES = frozenset({"LOOAL", "LOOAP"})

#: Point types that accept FAST / SLOW.
SPEED_TYPES = frozenset({"LFSSL", "LFSSP"})


# --------------------------------------------------------------------------
# Priorities
# --------------------------------------------------------------------------

#: Priority levels, lowest to highest. Manual, Chapter 3, Table 3-1.
#: A point is only commanded if the operation's priority is >= the point's.
PRIORITY_ORDER = ["@NONE", "@PDL", "@EMER", "@SMOKE", "@OPER"]
PRIORITY_RANK = {name: i for i, name in enumerate(PRIORITY_ORDER)}

PRIORITY_DESCRIPTIONS = {
    "@NONE": "PPCL priority (lowest) - the level PPCL commands act at by default",
    "@PDL": "Peak Demand Limiting",
    "@EMER": "Emergency",
    "@SMOKE": "Smoke control",
    "@OPER": "Operator (highest) - only an operator can release this",
}


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

RELATIONAL_OPS = {".EQ.", ".NE.", ".GT.", ".GE.", ".LT.", ".LE."}
LOGICAL_OPS = {".AND.", ".NAND.", ".OR.", ".XOR."}
DOTTED_OPS = RELATIONAL_OPS | LOGICAL_OPS | {".ROOT."}

#: Order of precedence, 1 = highest. Manual, Table 2-6.
#: Level 1 (parentheses) and level 2 (functions) are handled structurally.
PRECEDENCE = {
    ".ROOT.": 3,
    "*": 4,
    "/": 4,
    "+": 5,
    "-": 5,
    ".EQ.": 6,
    ".NE.": 6,
    ".GT.": 6,
    ".GE.": 6,
    ".LT.": 6,
    ".LE.": 6,
    ".AND.": 7,
    ".NAND.": 7,
    ".OR.": 8,
    ".XOR.": 8,
}

#: Maximum operands in a single PPCL statement.
#:
#: The two published figures disagree and both are recorded here rather than
#: reconciled. 125-1896 Rev. 5 (2000) states a maximum of 13 operands for an
#: IF expression. The Desigo CC PPCL Editor documentation states 16 operands
#: and 32 operators per statement, and that editor is the compiler a modern
#: PXC program is actually checked by. The linter uses the Desigo figure for
#: APOGEE firmware and the conservative Rev. 5 figure for the older families.
MAX_OPERANDS = {
    Firmware.APOGEE: 16,
    # The Desigo CC PPCL Editor is the compiler a PXC.A program is checked by,
    # and it states 16.
    Firmware.PXC_A: 16,
    Firmware.CM: 16,
    Firmware.LOGICAL: 13,
    Firmware.PHYSICAL: 13,
    Firmware.UNITARY: 13,
}

#: Maximum operators in a single PPCL statement (Desigo CC PPCL Editor).
MAX_OPERATORS = 32

#: Retained for the Rev. 5 figure specifically.
MAX_IF_OPERANDS = 13

#: Characters a PPCL program name may not contain, quoted exactly from the
#: Desigo CC "PPCL Guidelines" topic: "You can use any ASCII character except
#: the following: ? * [ ] { } |." Note that topic contradicts itself one
#: sentence earlier, where it says a program name may contain only letters,
#: numbers, periods and spaces. The explicit exclusion list is used here.
#:
#: A program name must also be unique across the entire building system, not
#: merely within its own field panel.
PROGRAM_NAME_INVALID = set("?*[]{}|")


# --------------------------------------------------------------------------
# Resident points, status indicators, functions
# --------------------------------------------------------------------------

#: System-maintained points that always exist in a panel's database.
RESIDENT_POINTS = {
    "ALMCNT": "Alarm count",
    "ALMCT2": "Alarm count 2",
    "$BATT": "Battery condition",
    "CRTIME": "Current time in decimal hours",
    "DAY": "Day of week",
    "DAYOFM": "Day of the month",
    "LINK": "Communications link status",
    "MONTH": "Month of year",
    "$PDL": "Peak Demand Limiting monitor",
    "SECNDS": "Seconds counter",
    "TIME": "Current time in military (24-hour) format",
}

#: NODE0..NODE99 and SECND1..SECND7 are generated ranges.
RESIDENT_RANGES = [("NODE", 0, 99), ("SECND", 1, 7)]

#: Point status indicators usable in comparisons.
STATUS_INDICATORS = {
    "ALARM",
    "ALMACK",
    "AUTO",
    "DEAD",
    "LOW",
    "OK",
    "DAYMOD",
    "FAILED",
    "FAST",
    "HAND",
    "NGTMOD",
    "OFF",
    "ON",
    "PRFON",
    "SLOW",
}

#: BACnet object types PPCL can reference on a BACnet panel. Desigo CC
#: engineering help, "BACnet Point Referencing". These sit alongside the
#: legacy L-prefixed point types rather than replacing them: a P2 panel still
#: uses LAI/LDO/LOOAP, a BACnet panel addresses BACnet objects.
BACNET_OBJECT_TYPES = {
    "AI": "Analog Input",
    "AO": "Analog Output",
    "AV": "Analog Value",
    "BI": "Binary Input",
    "BO": "Binary Output",
    "BV": "Binary Value",
    "MI": "Multi-State Input",
    "MO": "Multi-State Output",
    "MV": "Multi-State Value",
}

#: Commands Siemens documents as supported for Cross Trunk, i.e. reaching a
#: point on another BLN. Desigo CC engineering help, "PPCL Editor - APOGEE
#: Cross Trunk". The same topic adds: "Although PPCL may allow a Cross Trunk
#: reference in other PPCL commands, it is strongly recommended not to do
#: this."
CROSS_TRUNK_COMMANDS = frozenset(
    {
        "EMON", "EMOFF", "EMFAST", "EMSLOW", "EMAUTO", "EMSET",
        "ON", "OFF", "STATE", "AUTO", "FAST", "SLOW", "SET",
        "DAY", "NIGHT", "MAX", "MIN", "RELEAS",
    }
)

#: Cross Trunk issues at most one command per second per point; if several
#: arrive within a second only the last is sent.
CROSS_TRUNK_COMMANDS_PER_SECOND = 1

#: Third-party BACnet reference form: BAC_<device>_<objtype>_<instance>,
#: e.g. BAC_10_MO_1 is device instance 10, Multi-State Output, instance 1.
BACNET_REFERENCE = r"^BAC_(\d+)_([A-Z]{2})_(\d+)$"

#: Arithmetic + special functions, all single-argument. Manual, Table 2-6 L2.
#:
#: Arc-tangent is ATN. The Desigo CC precedence table spells it ``ARC(value1)``
#: but that is a documentation error: the PPCL Editor's own Command Assist
#: lists ATN and describes it as "a trigonometric function that calculates the
#: arc-tangent of a value ... expressed in degrees". ARC is deliberately NOT
#: accepted, so code using it is reported rather than passing review.
FUNCTIONS = {
    "ATN": "Arc-tangent (degrees)",
    "COM": "Complement",
    "COS": "Cosine (degrees)",
    "EXP": "Natural antilog",
    "LOG": "Natural log",
    "SIN": "Sine (degrees)",
    "SQRT": "Square root",
    "TAN": "Tangent (degrees)",
    "ALMPRI": "Alarm priority of a point (1-6)",
    "TOTAL": "Totalized value of a point",
}

#: The two entries in FUNCTIONS that Siemens calls *special functions* rather
#: than arithmetic ones: they read an attribute of a point rather than
#: computing on a number. "A special function is used to access a specific
#: value that is unique to a point... Since special functions are maintained
#: by the system, they cannot be manually commanded to a different value.
#: Special functions cannot be used over the network."
#: -- Insight Program Editor, What Are Special Functions.
SPECIAL_FUNCTIONS = frozenset({"ALMPRI", "TOTAL"})

#: Local variable name ranges available to every program.
LOCAL_ARG_COUNT = 15  # $ARG1..$ARG15
LOCAL_LOC_COUNT = 15  # $LOC1..$LOC15


# --------------------------------------------------------------------------
# BACnet properties
# --------------------------------------------------------------------------

#: ``{number: (short_name, description)}`` for every property GETVAL and
#: SETVAL can reach. Transcribed from A6V10374898 "PXC.A PPCL User Guide",
#: Appendix B, "Property Short Names, Numbers, and Descriptions".
#:
#: Numbers below ~512 are standard BACnetPropertyIdentifier values; the 4000s
#: and 5000s are Siemens' proprietary range.
#:
#: Appendix B states the short names "must be preceded by @ (for example,
#: @RefVal)", while the GETVAL examples in Chapter 3 write them bare. Nothing
#: available here settles which the compiler enforces, so both spellings are
#: accepted and the @ form is what this toolkit emits.
BACNET_PROPERTIES = {
    6: ("RefVal", "Reference value"),
    15: ("CosCnt", "Change-of-state count"),
    17: ("NotifCl", "Notification class"),
    22: ("Cov", "Change of value, COV"),
    24: ("DsavSta", "DST status"),
    25: ("HysToNrml", "Hysteresis to normal"),
    33: ("EldActvTi", "Operating hours"),
    36: ("EvtSta", "Event state"),
    40: ("FbVal", "Feedback value"),
    45: ("HiLm", "High limit"),
    59: ("LoLm", "Low limit"),
    65: ("PrValMax", "Present maximum value"),
    66: ("TiOffMin", "Minimum switch-off time"),
    67: ("TiOnMin", "Minimum switch-on time"),
    69: ("PrValMin", "Present minimum value"),
    72: ("NotifTyp", "Notification type"),
    74: ("NumSta", "Number of states"),
    81: ("OoServe", "Out of service"),
    84: ("Pol", "Polarity"),
    85: ("PrVal", "Present value"),
    86: ("Prio", "Priority"),
    87: ("PrioArr", "Priority array"),
    90: ("PgmChg", "Program change"),
    92: ("PgmSta", "Program state"),
    98: ("ProtVn", "Protocol version"),
    99: ("ReadOnly", "Read-only"),
    103: ("Rlb", "Reliability"),
    104: ("DefCmd", "Default command"),
    106: ("Rsl", "Resolution"),
    112: ("SysSta", "System state"),
    113: ("TiMonDvn", "Monitoring time deviation"),
    117: ("Un", "Units"),
    120: ("VendrId", "Vendor identifier"),
    133: ("Enable", "Enable logging"),
    139: ("ProtRv", "Protocol revision"),
    155: ("DbRv", "Database revision"),
    173: ("LstRecNr", "Last notified record number"),
    196: ("LstRstRn", "Last restart reason"),
    353: ("EnEvtDet", "Enable event detection"),
    356: ("TiMonDvnNm", "Monitoring time deviation to normal"),
    4354: ("StupDef", "Startup default"),
    4432: ("PrValTrunc", "Present value truncated"),
    4433: ("PrValScale", "Present value scaled"),
    4482: ("MntnSta", "Maintenance state"),
    4483: ("EldTiStaPrv", "Elapsed active time per state, previous period"),
    4484: ("EldTiStaPr", "Elapsed active time per state, present period"),
    4485: ("EldTiSta", "Elapsed active time per state"),
    4486: ("EldTiRngPrv", "Elapsed active time per range, previous period"),
    4487: ("EldTiRngPr", "Elapsed active time per range, present period"),
    4488: ("EldTiRng", "Elapsed active time per range"),
    4489: ("NumRanges", "Number of ranges"),
    4490: ("Oph", "Operating hours"),
    4491: ("DvnHiValPrv", "Deviation high value, previous period"),
    4493: ("DvnHiValPr", "Deviation high value, present period"),
    4494: ("DvnLoValPr", "Deviation low value, present period"),
    4495: ("RefHiValDvn", "Reference high value for deviation"),
    4496: ("RefLoValDvn", "Reference low value for deviation"),
    4498: ("ValMaxPrv", "Maximum value, previous period"),
    4499: ("ValMinPrv", "Minimum value, previous period"),
    4500: ("AvrgValPr", "Average value, present period"),
    4501: ("ValMaxPr", "Maximum value, present period"),
    4502: ("ValMinPr", "Minimum value, present period"),
    4509: ("EldTiLmPr", "Elapsed active time limit, present period"),
    4510: ("EldTiLm", "Elapsed active time limit"),
    4511: ("EldTiPrvPrd", "Elapsed active time, previous period"),
    4512: ("EldTiPrPrd", "Elapsed active time, present period"),
    4513: ("AggtnPrd", "Aggregation period"),
    4514: ("EnAggtn", "Enable aggregation"),
    4558: ("AlarmExtn", "Event configuration"),
    4580: ("AggtnExtn", "Aggregation: operating hours and others"),
    4947: ("SysUnits", "System of units"),
    4968: ("SigTyp", "Signal type"),
    4972: ("SupEvtNotf", "Suppress event notification"),
    4974: ("TiMonDvnFt", "Monitoring time deviation to fault"),
    5000: ("SubsysExtn", "Subsystem"),
    5040: ("Tz", "Time zone"),
    5053: ("Rlb", "Reliability"),
    5093: ("PrPrio", "Present priority"),
    5094: ("UpdCnt", "Update count"),
    5105: ("CorrFac", "Correction factor"),
    5106: ("CorrOfs", "Correction offset"),
    5107: ("TckVal", "Tracking value"),
    5113: ("AvlFlash", "Available flash memory"),
    5114: ("AvlRAM", "Available RAM"),
    5127: ("TotlizdVal", "Totalized value"),
    5129: ("ActTiScld", "Active time scaled"),
    5130: ("InactTiScld", "Inactive time scaled"),
    5133: ("TotListVal", "Totalized list value"),
    5134: ("TiTotLVIRst", "Time of totalized list value reset"),
    5135: ("TotliznRate", "Totalization rate"),
    5136: ("TotPrvRtVal", "Totalized previous reset value"),
    5137: ("PrvAcRstVal", "Previous active reset value"),
    5138: ("PrvInRstVal", "Previous inactive reset value"),
    5139: ("TotPrvRLVal", "Totalized previous reset list value"),
    5165: ("MaxPPCLChs", "Maximum PPCL line characters"),
    5167: ("PPCLPgmPrio", "PPCL program priority"),
    5194: ("SnrSharing", "Sensor sharing"),
    5213: ("DevInhNotif", "Device inhibit alarm notifications"),
    5215: ("AggrType", "Aggregation type"),
}

#: ``{SHORTNAME: number}``. Two numbers share the short name ``Rlb`` (103 and
#: 5053); the standard-range one wins, which is what a program naming ``@Rlb``
#: will mean in practice.
PROPERTY_NUMBERS = {}
for _number in sorted(BACNET_PROPERTIES):
    _short, _ = BACNET_PROPERTIES[_number]
    PROPERTY_NUMBERS.setdefault(_short.upper(), _number)
del _number, _short

#: Properties whose value changes how a point behaves rather than what it
#: reads, so a SETVAL to one of them is invisible in the way an @EMER command
#: is not. Rule W336 reports them.
DANGEROUS_PROPERTIES = {
    81: "takes the point out of service, so it stops tracking its input and "
        "no longer looks commanded",
    87: "writes the BACnet priority array directly, bypassing the priority "
        "arbitration that @-priority commands go through",
    99: "makes the point read-only",
    104: "changes the value the point falls back to when nothing commands it",
    4354: "changes the value the point starts up at",
}


def property_number(name) -> int:
    """Resolve ``@PrVal``, ``PrVal`` or ``85`` to a property number, or None."""
    if isinstance(name, (int, float)) and float(name) == int(name):
        number = int(name)
        return number if number in BACNET_PROPERTIES else None
    text = str(name).strip().strip('"').lstrip("@")
    if text.isdigit():
        number = int(text)
        return number if number in BACNET_PROPERTIES else None
    return PROPERTY_NUMBERS.get(text.upper())


def property_name(number) -> str:
    """The short name for a property number, or None."""
    entry = BACNET_PROPERTIES.get(int(number)) if number is not None else None
    return entry[0] if entry else None


# --------------------------------------------------------------------------
# Command signatures
# --------------------------------------------------------------------------


class Arg(str, Enum):
    """Kind of value a command parameter accepts."""

    POINT = "point"  # must be a point name or local variable
    VALUE = "value"  # point name, local variable, or number
    NUMBER = "number"  # numeric literal, point, or local
    INTEGER = "integer"  # must be an integer literal
    DECIMAL = "decimal"  # must carry a decimal point (integers rejected)
    LINE = "line"  # a PPCL line number
    TIME = "time"  # military/decimal time, point, or local
    PRIORITY = "priority"  # an @-priority indicator
    STRING = "string"  # a quoted literal string
    ENUM = "enum"  # one of a fixed set of values


@dataclass(frozen=True)
class Param:
    """One positional parameter of a command."""

    name: str
    kind: Arg
    doc: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class Command:
    """A PPCL command signature.

    ``fixed`` are the leading parameters that must always be present.
    ``repeat`` is a tuple of parameters forming a repeating group (for example
    the ``x,y`` pairs of TABLE), repeated between ``min_repeat`` and
    ``max_repeat`` times.

    ``priority_arg`` records that the command accepts an optional leading
    ``@prior`` parameter (a revision 9.2+ logical / CM / APOGEE feature). When
    the priority is supplied it consumes one parameter slot, which is why the
    manual lists ON as 16 points without a priority but 15 with one.
    """

    name: str
    summary: str
    fixed: tuple = ()
    repeat: tuple = ()
    #: Fixed parameters that follow the repeating group. Only LSQ2 needs this,
    #: with its trailing startline#/endline# after a variable list of points.
    trailing: tuple = ()
    min_repeat: int = 0
    max_repeat: int = 0
    priority_arg: bool = False
    firmware: frozenset = frozenset(Firmware)
    point_types: frozenset = frozenset()
    time_based: bool = False  # must be evaluated every pass
    subroutine_safe: bool = True  # may appear inside a GOSUB body
    if_target_safe: bool = True  # may be the THEN/ELSE clause of an IF
    #: False when the command is known to exist but its argument list is not
    #: published in any source available here. Argument checking is skipped
    #: rather than guessed at, so real code using it is not falsely flagged.
    signature_known: bool = True
    notes: tuple = ()
    see_also: tuple = ()

    def max_args(self) -> int:
        """Largest legal argument count, including an optional @priority."""
        base = (
            len(self.fixed)
            + len(self.repeat) * self.max_repeat
            + len(self.trailing)
        )
        return base + (1 if self.priority_arg else 0)

    def min_args(self) -> int:
        """Smallest legal argument count, excluding an optional @priority."""
        return (
            len(self.fixed)
            + len(self.repeat) * self.min_repeat
            + len(self.trailing)
        )


def _points(n_min: int, n_max: int, doc: str = "point") -> dict:
    """Helper for the many commands shaped ``CMD(pt1,...,ptN)``."""
    return {
        "repeat": (Param("pt", Arg.POINT, doc),),
        "min_repeat": n_min,
        "max_repeat": n_max,
    }


def _lines(n_min: int, n_max: int) -> dict:
    """Helper for the ACT/DEACT/ENABLE/DISABL family."""
    return {
        "repeat": (Param("line", Arg.LINE, "PPCL line number"),),
        "min_repeat": n_min,
        "max_repeat": n_max,
    }


ALL = {}


def _add(cmd: Command) -> None:
    ALL[cmd.name] = cmd


# -- line activation -------------------------------------------------------

_add(
    Command(
        "ACT",
        "Activate (enable) up to 16 specific PPCL lines",
        **_lines(1, 16),
        notes=(
            "Only affects lines in the same device.",
            "A range cannot be given; each line must be listed separately.",
        ),
        see_also=("DEACT", "DISABL", "ENABLE"),
    )
)
_add(
    Command(
        "ENABLE",
        "Enable up to 16 specific PPCL lines (interchangeable with ACT)",
        **_lines(1, 16),
        notes=(
            "Only affects lines in the same device.",
            "A range cannot be given; each line must be listed separately.",
        ),
        see_also=("ACT", "DEACT", "DISABL"),
    )
)
_add(
    Command(
        "DEACT",
        "Deactivate (disable) up to 16 specific PPCL lines",
        **_lines(1, 16),
        notes=("Only affects lines in the same device.",),
        see_also=("ACT", "DISABL", "ENABLE"),
    )
)
_add(
    Command(
        "DISABL",
        "Disable up to 16 specific PPCL lines (interchangeable with DEACT)",
        **_lines(1, 16),
        notes=("Only affects lines in the same device.",),
        see_also=("ACT", "DEACT", "ENABLE"),
    )
)

# -- alarming --------------------------------------------------------------

_add(
    Command(
        "ALARM",
        "Force up to 16 points into the ALARM state",
        **_points(1, 16),
        notes=(
            "Points must be defined as alarmable and enabled for alarming.",
            "Displays status *AC* when commanded to ALARM.",
            "Points must reside in the same device; local variables are invalid.",
        ),
        see_also=("DISALM", "ENALM", "HLIMIT", "LLIMIT", "NORMAL"),
    )
)
_add(
    Command(
        "NORMAL",
        "Return up to 16 points from alarm-by-command to normal",
        **_points(1, 16),
        see_also=("ALARM", "DISALM", "ENALM", "HLIMIT", "LLIMIT"),
    )
)
_add(
    Command(
        "DISALM",
        "Disable alarm reporting for up to 16 points",
        **_points(1, 16),
        notes=(
            "Point status changes to *PDSB*.",
            "Cannot be used across the network.",
        ),
        see_also=("ALARM", "ENALM", "HLIMIT", "LLIMIT", "NORMAL"),
    )
)
_add(
    Command(
        "ENALM",
        "Enable alarm reporting for up to 16 points",
        **_points(1, 16),
        notes=(
            "Reverses DISALM. Does not override *ODSB*.",
            "Cannot be used across the network.",
        ),
        see_also=("ALARM", "DISALM", "HLIMIT", "LLIMIT", "NORMAL"),
    )
)
_add(
    Command(
        "HLIMIT",
        "Set a new high alarm limit on up to 15 analog points",
        fixed=(
            Param("value", Arg.DECIMAL, "New high limit; integers are NOT allowed"),
        ),
        **_points(1, 15),
        notes=(
            "Points must be defined as alarmable and reside in the same device.",
            "Forces a PPCL upload to mass storage on pre-Rev 11.1 logical firmware.",
        ),
        see_also=("ALARM", "DISALM", "ENALM", "LLIMIT", "NORMAL"),
    )
)
_add(
    Command(
        "LLIMIT",
        "Set a new low alarm limit on up to 15 analog points",
        fixed=(
            Param("value", Arg.DECIMAL, "New low limit; integers are NOT allowed"),
        ),
        **_points(1, 15),
        notes=(
            "Points must be defined as alarmable and reside in the same device.",
            "Forces a PPCL upload to mass storage on pre-Rev 11.1 logical firmware.",
        ),
        see_also=("ALARM", "DISALM", "ENALM", "HLIMIT", "NORMAL"),
    )
)

# -- commanding ------------------------------------------------------------

_add(
    Command(
        "ON",
        "Command up to 16 points to the ON state",
        **_points(1, 16),
        priority_arg=True,
        point_types=frozenset({"LDO", "L2SL", "L2SP", "LOOAL", "LOOAP"}),
        notes=("With an @priority the maximum drops to 15 points.",),
        see_also=("AUTO", "FAST", "OFF", "SLOW"),
    )
)
_add(
    Command(
        "OFF",
        "Command up to 16 points to the OFF state",
        **_points(1, 16),
        priority_arg=True,
        point_types=frozenset(
            {"LDI", "LDO", "L2SL", "L2SP", "LOOAL", "LOOAP", "LFSSL", "LFSSP"}
        ),
        notes=(
            "With an @priority the maximum drops to 15 points.",
            "FAST/SLOW/STOP points (LFSSL, LFSSP) use OFF for STOP.",
        ),
        see_also=("AUTO", "FAST", "ON", "SLOW"),
    )
)
_add(
    Command(
        "AUTO",
        "Set up to 16 ON/OFF/AUTO points to AUTO",
        **_points(1, 16),
        point_types=AUTO_TYPES,
        notes=("Valid only for LOOAL and LOOAP points.",),
        see_also=("FAST", "OFF", "ON", "SLOW"),
    )
)
_add(
    Command(
        "FAST",
        "Set up to 16 FAST/SLOW/STOP points to FAST",
        **_points(1, 16),
        priority_arg=True,
        point_types=SPEED_TYPES,
        notes=("With an @priority the maximum drops to 15 points.",),
        see_also=("AUTO", "OFF", "ON", "SLOW"),
    )
)
_add(
    Command(
        "SLOW",
        "Set up to 16 FAST/SLOW/STOP points to SLOW",
        **_points(1, 16),
        priority_arg=True,
        point_types=SPEED_TYPES,
        notes=("With an @priority the maximum drops to 15 points.",),
        see_also=("AUTO", "FAST", "OFF", "ON"),
    )
)
_add(
    Command(
        "SET",
        "Command up to 15 output points to an analog value",
        fixed=(
            Param(
                "value",
                Arg.DECIMAL,
                "Value to command; integers allowed only on APOGEE firmware",
            ),
        ),
        **_points(1, 15),
        priority_arg=True,
        point_types=frozenset(
            {
                "LAO",
                "LDO",
                "LFSSL",
                "LFSSP",
                "LOOAL",
                "LOOAP",
                "L2SL",
                "L2SP",
                "LPACI",
            }
        ),
        notes=(
            "With an @priority the maximum drops to 14 points.",
            "The @priority precedes the value: SET(@EMER,75.0,PT1).",
        ),
    )
)
_add(
    Command(
        "STATE",
        "Command up to 15 points using a state-text value",
        fixed=(
            Param("statetext", Arg.VALUE, "Text from the point's state-text table"),
        ),
        **_points(1, 15),
        priority_arg=True,
        notes=("With an @priority the maximum drops to 14 points.",),
    )
)
_add(
    Command(
        "RELEAS",
        "Release up to 16 points to NONE priority",
        **_points(1, 16),
        priority_arg=True,
        notes=(
            "Always release at a priority at least as high as the point's current "
            "priority, or the release silently fails.",
            "A point commanded from the keyboard requires @OPER to release.",
            "With an @priority the maximum drops to 15 points.",
        ),
    )
)
_add(
    Command(
        "DAY",
        "Command up to 16 logical controller points to DAY (occupied) mode",
        **_points(1, 16),
        point_types=frozenset({"LCTLR"}),
        notes=("Some equipment controllers call DAY mode OCC.",),
        see_also=("NIGHT",),
    )
)
_add(
    Command(
        "NIGHT",
        "Command up to 16 logical controller points to NIGHT (unoccupied) mode",
        **_points(1, 16),
        point_types=frozenset({"LCTLR"}),
        notes=("Some equipment controllers call NIGHT mode UNOCC.",),
        see_also=("DAY",),
    )
)

# -- emergency-priority commanding ----------------------------------------

_add(
    Command(
        "EMON",
        "Command up to 16 points ON at emergency priority",
        **_points(1, 16),
        notes=(
            "Physical firmware form is EMON(prior,pt1) where prior is 501 (EMER) "
            "or 32523 (NONE).",
        ),
        see_also=("EMAUTO", "EMFAST", "EMOFF", "EMSET", "EMSLOW"),
    )
)
_add(
    Command(
        "EMOFF",
        "Command up to 16 points OFF at emergency priority",
        **_points(1, 16),
        notes=(
            "Physical firmware form is EMOFF(prior,pt1) where prior is 501 (EMER) "
            "or 32523 (NONE).",
        ),
        see_also=("EMAUTO", "EMFAST", "EMON", "EMSET", "EMSLOW"),
    )
)
_add(
    Command(
        "EMAUTO",
        "Command up to 16 LOOAL/LOOAP points to AUTO at emergency priority",
        **_points(1, 16),
        point_types=AUTO_TYPES,
        see_also=("EMFAST", "EMOFF", "EMON", "EMSET", "EMSLOW"),
    )
)
_add(
    Command(
        "EMFAST",
        "Command up to 16 LFSSL/LFSSP points to FAST at emergency priority",
        **_points(1, 16),
        point_types=SPEED_TYPES,
        see_also=("EMAUTO", "EMOFF", "EMON", "EMSET", "EMSLOW"),
    )
)
_add(
    Command(
        "EMSLOW",
        "Command up to 16 LFSSL/LFSSP points to SLOW at emergency priority",
        **_points(1, 16),
        point_types=SPEED_TYPES,
        see_also=("EMAUTO", "EMFAST", "EMOFF", "EMON", "EMSET"),
    )
)
_add(
    Command(
        "EMSET",
        "Set up to 15 analog points to a value at emergency priority",
        fixed=(Param("value", Arg.VALUE, "Analog value to command"),),
        **_points(1, 15),
        point_types=ANALOG_TYPES,
        see_also=("EMAUTO", "EMFAST", "EMOFF", "EMON", "EMSLOW"),
    )
)

# -- change-of-value and telephone ----------------------------------------

_add(
    Command(
        "DISCOV",
        "Disable Change-Of-Value reporting for up to 16 points",
        **_points(1, 16),
        notes=(
            "Stops graphics updates, archiving, COV printing and alarming.",
            "Points must reside in the same device.",
        ),
        see_also=("ENCOV",),
    )
)
_add(
    Command(
        "ENCOV",
        "Enable Change-Of-Value reporting for up to 16 points",
        **_points(1, 16),
        notes=("Points must reside in the same device.",),
        see_also=("DISCOV",),
    )
)
_add(
    Command(
        "DPHONE",
        "Disable up to 16 telephone ID numbers",
        repeat=(Param("phone_id", Arg.INTEGER, "Telephone ID defined in the device"),),
        min_repeat=1,
        max_repeat=16,
        notes=("Cannot be used over a network.",),
        see_also=("EPHONE",),
    )
)
_add(
    Command(
        "EPHONE",
        "Enable up to 16 telephone ID numbers",
        repeat=(Param("phone_id", Arg.INTEGER, "Telephone ID defined in the device"),),
        min_repeat=1,
        max_repeat=16,
        notes=("Cannot be used over a network.",),
        see_also=("DPHONE",),
    )
)

# -- program flow ----------------------------------------------------------

_add(
    Command(
        "GOTO",
        "Branch program execution to another line",
        fixed=(Param("line", Arg.LINE, "Target line number"),),
        notes=(
            "Should transfer control to a sequentially HIGHER line number, "
            "otherwise the program can be caught in an endless loop.",
            "If the target line does not exist, execution continues at the next "
            "line after the specified number.",
            "Should not transfer control to a comment line.",
            "Must not be used to jump back to the top of a program: skipping the "
            "last line breaks time-based commands (LOOP, WAIT, ...).",
        ),
        see_also=("GOSUB",),
    )
)
_add(
    Command(
        "GOSUB",
        "Call a subroutine, optionally passing up to 15 arguments",
        fixed=(Param("line", Arg.LINE, "First line of the subroutine"),),
        repeat=(Param("arg", Arg.POINT, "Value passed as $ARGn"),),
        min_repeat=0,
        max_repeat=15,
        if_target_safe=False,
        notes=(
            "Parentheses around the arguments are optional.",
            "Arguments become $ARG1..$ARG15 inside the subroutine.",
            "The subroutine's last line must be RETURN.",
            "A GOSUB cannot be used inside an IF/THEN/ELSE statement.",
            "A GOSUB may only reference point names or local variables.",
        ),
        see_also=("RETURN", "GOTO"),
    )
)
_add(
    Command(
        "RETURN",
        "Mark the end of a subroutine and return to the caller",
        notes=("Must be the last command of any GOSUB subroutine.",),
        see_also=("GOSUB",),
    )
)
_add(
    Command(
        "ONPWRT",
        "Set the line execution resumes at after a power failure",
        fixed=(Param("line", Arg.LINE, "Line to resume at"),),
        notes=(
            "Should be the FIRST command in a PPCL program, because execution "
            "otherwise returns to the program's first line after a power failure.",
            "Executed once and then ignored while power stays on.",
            "Not executed if the panel database was lost in the power failure.",
        ),
    )
)

# -- timing ----------------------------------------------------------------

_add(
    Command(
        "SAMPLE",
        "Evaluate the rest of the statement only every N seconds",
        fixed=(Param("sec", Arg.INTEGER, "Interval in seconds, 1 to 32767"),),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "The remainder of the line must not contain its own timing function "
            "(WAIT, PDL, TOD, TIMAVG, LOOP, SSTO, or another SAMPLE).",
            "Executes immediately on return from power failure, after ENABLE, or "
            "on the first PPCL pass after a database load.",
        ),
    )
)
_add(
    Command(
        "WAIT",
        "Command a digital point N seconds after a trigger point changes",
        fixed=(
            Param("time", Arg.INTEGER, "Delay in seconds, 1 to 32767"),
            Param("pt1", Arg.POINT, "Digital trigger point"),
            Param("pt2", Arg.POINT, "Digital point to command"),
            Param(
                "mode",
                Arg.ENUM,
                "11/10/01/00 - trigger edge and result state",
                choices=("11", "10", "01", "00"),
            ),
        ),
        time_based=True,
        subroutine_safe=False,
        if_target_safe=False,
        point_types=frozenset({"LDI", "LDO", "L2SL", "L2SP", "LOOAL", "LOOAP"}),
        notes=(
            "Physical firmware omits the mode parameter: WAIT(time,pt1,pt2).",
            "Mode 11 = on trigger ON wait then turn pt2 ON; 10 = on ON wait then "
            "OFF; 01 = on OFF wait then ON; 00 = on OFF wait then OFF.",
            "After a power failure or ENABLE, the trigger must toggle before the "
            "command executes, regardless of current point states.",
        ),
    )
)
_add(
    Command(
        "TIMAVG",
        "Rolling average of an analog point over N samples",
        fixed=(
            Param("result", Arg.POINT, "Point storing the average"),
            Param("st", Arg.INTEGER, "Seconds between samples"),
            Param("samples", Arg.INTEGER, "Number of samples, 1 to 10"),
            Param("input", Arg.POINT, "LAI or LAO point to average"),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "The result updates every sample time, not every program pass.",
            "On power-up the result equals the current input value.",
        ),
    )
)

# -- control ---------------------------------------------------------------

_add(
    Command(
        "LOOP",
        "PID closed-loop control",
        fixed=(
            Param(
                "type",
                Arg.ENUM,
                "0 = direct acting, 128 = reverse acting",
                choices=("0", "128"),
            ),
            Param("pv", Arg.POINT, "Process variable being controlled"),
            Param("cv", Arg.POINT, "Control variable (loop output)"),
            Param("sp", Arg.VALUE, "Set point"),
            Param("pg", Arg.NUMBER, "Proportional gain"),
            Param("ig", Arg.NUMBER, "Integral gain (0 to disable)"),
            Param("dg", Arg.NUMBER, "Derivative gain (0 to disable)"),
            Param("st", Arg.NUMBER, "Sample time in seconds, minimum 1"),
            Param("bias", Arg.NUMBER, "Output when pv equals sp"),
            Param("lo", Arg.NUMBER, "Low limit of loop output"),
            Param("hi", Arg.NUMBER, "High limit of loop output"),
            Param("reserved", Arg.ENUM, "Not used; must be 0", choices=("0",)),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "pg = (full range of controlled device / throttling range) * 1000.",
            "A recommended integral gain starting point is 2% of pg (ig = pg*.02).",
            "bias should lie between lo and hi.",
            "Anti-windup is automatic once hi or lo is reached.",
        ),
    )
)
#: Read and write a BACnet property. New in the PXC.A line and absent from the
#: 2000 APOGEE manual entirely -- both come from A6V10374898 "PXC.A PPCL User
#: Guide", Chapter 3. They are the only way to reach a property other than the
#: present value from PPCL, which is why they matter on a BACnet site.
_add(
    Command(
        "GETVAL",
        "Read a property from an object into another object",
        fixed=(
            Param("targObjRef", Arg.POINT,
                  "Object receiving the value, e.g. BAC_7_AO_11 or KwMeter"),
            Param("srcObjRef", Arg.POINT, "Object to read from"),
            Param("srcPropSpec", Arg.VALUE,
                  "Property: a numeric BACnetPropertyIdentifier from the "
                  "standard or proprietary range, or an abbreviated property "
                  "name such as @PrVal"),
        ),
        trailing=(
            Param("srcIndex", Arg.INTEGER,
                  "Optional array index, for an array-valued property"),
        ),
        notes=(
            "Only properties with numeric data types, or arrays of numeric "
            "data types, can be read or written from PPCL.",
            "The PPCL engine converts between Real, Unsigned and Integer.",
            "Property short names are listed in BACNET_PROPERTIES. Appendix B "
            "says they must be written with a leading @; the manual's own "
            "GETVAL examples write them bare. Both are accepted here.",
            "The two examples printed in Chapter 3 are malformed -- one has a "
            "stray closing parenthesis, the other never closes. The parameter "
            "table is authoritative.",
        ),
        see_also=("SETVAL", "SET"),
    )
)
_add(
    Command(
        "SETVAL",
        "Write a property value into up to 14 objects",
        fixed=(
            Param("srcValSpec", Arg.VALUE,
                  "Value to write: a number, a reserved word for an "
                  "enumerated value, or an object reference"),
            Param("targPropSpec", Arg.VALUE,
                  "Property: a numeric BACnetPropertyIdentifier, or an "
                  "abbreviated property name such as @OoServe"),
        ),
        repeat=(Param("targObjRef", Arg.POINT, "Object to write to"),),
        min_repeat=1,
        max_repeat=14,
        notes=(
            "Only properties with numeric data types, or arrays of numeric "
            "data types, can be read or written from PPCL.",
            "Writing @OoServe (81) takes a point out of service, and writing "
            "@PrioArr (87) manipulates the BACnet priority array directly. "
            "Neither leaves the point looking commanded, so neither is visible "
            "the way an @EMER command is. Rule W336 flags both.",
            "The PPCL engine converts between Real, Unsigned and Integer.",
        ),
        see_also=("GETVAL", "SET", "RELEAS"),
    )
)
_add(
    Command(
        "TABLE",
        "Piecewise-linear transfer function between two points",
        fixed=(
            Param("input", Arg.POINT, "Input (x) variable"),
            Param("output", Arg.POINT, "Output (y) variable"),
        ),
        repeat=(
            Param("x", Arg.NUMBER, "x coordinate"),
            Param("y", Arg.NUMBER, "y coordinate"),
        ),
        min_repeat=1,
        max_repeat=7,
        notes=(
            "x values must be entered in ascending order.",
            "For inputs below x1 the output is y1; above the last x it is the "
            "last y. The command interpolates linearly in between.",
            "Tables can be cascaded by overlapping x-y pairs via virtual points.",
        ),
    )
)
_add(
    Command(
        "DBSWIT",
        "Dead-band switch: software thermostat",
        fixed=(
            Param(
                "type",
                Arg.ENUM,
                "0 = ON above high / OFF below low; 1 = ON below low / OFF above high",
                choices=("0", "1"),
            ),
            Param("input", Arg.POINT, "Analog point driving the switch"),
            Param("low", Arg.NUMBER, "Low switching limit"),
            Param("high", Arg.NUMBER, "High switching limit"),
        ),
        **_points(1, 12),
    )
)
_add(
    Command(
        "MAX",
        "Store the largest of up to 15 values",
        fixed=(Param("result", Arg.POINT, "Point receiving the largest value"),),
        repeat=(Param("value", Arg.VALUE, "Value to compare"),),
        min_repeat=2,
        max_repeat=15,
        notes=("Numeric literals must be in decimal format.",),
        see_also=("MIN",),
    )
)
_add(
    Command(
        "MIN",
        "Store the smallest of up to 15 values",
        fixed=(Param("result", Arg.POINT, "Point receiving the smallest value"),),
        repeat=(Param("value", Arg.VALUE, "Value to compare"),),
        min_repeat=2,
        max_repeat=15,
        notes=("Numeric literals must be in decimal format.",),
        see_also=("MAX",),
    )
)
_add(
    Command(
        "INITTO",
        "Reset the totalized value of up to 15 points",
        fixed=(
            Param(
                "value", Arg.DECIMAL, "New totalized value; write it as a decimal"
            ),
        ),
        **_points(1, 15),
        notes=(
            "Points must be defined for totalization and reside in the same device.",
            "Cannot reset LPACI point types.",
            "On APOGEE panels this resets ALL totalized states of a digital point.",
            "Sources disagree on whether an integer is accepted. 125-1896 says "
            "it is not; the Insight Program Editor help says only 'a number, "
            "point name, or local variable', and field programs do use plain "
            "0. W113 is a warning, not an error, for that reason.",
        ),
    )
)

# -- duty cycling ----------------------------------------------------------

_add(
    Command(
        "DC",
        "Duty cycle up to 8 points on a 15-minute pattern",
        repeat=(
            Param("pt", Arg.POINT, "Output point to duty cycle"),
            Param("pat", Arg.INTEGER, "Four digits 0-7, read right to left"),
        ),
        min_repeat=1,
        max_repeat=8,
        point_types=frozenset({"LDO", "LOOAL", "LOOAP", "L2SL", "L2SP"}),
        notes=(
            "Each pattern digit encodes three 5-minute ON/OFF slots of a "
            "15-minute segment; digits are read right to left.",
            "DC runs at NONE priority, so guard it with IF/THEN/ELSE to prevent "
            "conflicts with other NONE-priority commands.",
        ),
        see_also=("DCR",),
    )
)
_add(
    Command(
        "DCR",
        "Duty cycle up to 4 points against a temperature dead band",
        repeat=(
            Param("pt", Arg.POINT, "Output point to duty cycle"),
            Param("temp", Arg.POINT, "Space temperature point"),
            Param("high", Arg.NUMBER, "High temperature limit"),
            Param("low", Arg.NUMBER, "Low temperature limit"),
        ),
        min_repeat=1,
        max_repeat=4,
        notes=(
            "An ON/OFF decision is made every 5 minutes.",
            "DCR runs at NONE priority, so guard it with IF/THEN/ELSE.",
        ),
        see_also=("DC",),
    )
)

# -- time of day -----------------------------------------------------------

_add(
    Command(
        "TOD",
        "Schedule digital points ON and OFF by day mode and time",
        fixed=(
            Param("mode", Arg.INTEGER, "Sum of 1/2/4/8/16 schedule modes"),
            Param(
                "recomd",
                Arg.ENUM,
                "1 = recommand after power failure, 0 = do not",
                choices=("0", "1"),
            ),
            Param("time1", Arg.TIME, "Time the ON command runs"),
            Param("time2", Arg.TIME, "Time the OFF command runs"),
        ),
        **_points(1, 12),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "Mode 16 (holiday) should only be used together with HOLIDA.",
            "A relative time point must hold a time at or after the current time "
            "or the command will not execute correctly.",
        ),
        see_also=("HOLIDA", "SSTO", "TODMOD", "TODSET"),
    )
)
_add(
    Command(
        "TODSET",
        "Schedule analog points to values by day mode and time",
        fixed=(
            Param("mode", Arg.INTEGER, "Sum of 1/2/4/8/16 schedule modes"),
            Param(
                "recomd",
                Arg.ENUM,
                "1 = recommand after power failure, 0 = do not",
                choices=("0", "1"),
            ),
            Param("time1", Arg.TIME, "Time points are commanded to val1"),
            Param("val1", Arg.VALUE, "Value applied at time1"),
            Param("time2", Arg.TIME, "Time points are commanded to val2"),
            Param("val2", Arg.VALUE, "Value applied at time2"),
        ),
        **_points(1, 10),
        time_based=True,
        subroutine_safe=False,
        notes=("Mode 16 (holiday) should only be used together with HOLIDA.",),
        see_also=("HOLIDA", "TOD", "TODMOD"),
    )
)
_add(
    Command(
        "TODMOD",
        "Assign a schedule mode to each day of the week",
        fixed=tuple(
            Param(
                d, Arg.ENUM, "Mode for this day: 1, 2, 4 or 8", choices=("1", "2", "4", "8")
            )
            for d in (
                "momode",
                "tumode",
                "wemode",
                "thmode",
                "frmode",
                "samode",
                "sumode",
            )
        ),
        time_based=True,
        subroutine_safe=False,
        if_target_safe=False,
        notes=(
            "Mode 16 is NOT entered here; it is set automatically on a HOLIDA date.",
            "TODMOD and HOLIDA must precede any TOD or TODSET command.",
            "Only affects TOD/TODSET/SSTO commands in the same device.",
        ),
        see_also=("HOLIDA", "TOD", "TODSET"),
    )
)
_add(
    Command(
        "HOLIDA",
        "Define up to 8 holiday dates",
        repeat=(
            Param("month", Arg.INTEGER, "Month, 1-12"),
            Param("day", Arg.INTEGER, "Day of month, 1-31"),
        ),
        min_repeat=1,
        max_repeat=8,
        notes=(
            "Multiple HOLIDA commands may be used for more than 8 holidays.",
            "HOLIDA and TODMOD must precede any TOD or TODSET command.",
            "A HOLIDA date forces that day's TODMOD mode to 16.",
            "If holidays are ALSO defined in the panel's TOD calendar, make sure "
            "both lists match, or equipment will run the holiday schedule on more "
            "days than intended.",
        ),
        see_also=("TOD", "TODMOD", "TODSET"),
    )
)

# -- start/stop time optimisation -----------------------------------------

_add(
    Command(
        "SSTO",
        "Calculate optimum start and stop times for a zone",
        fixed=(
            Param("zone", Arg.INTEGER, "SSTO zone number, 1-5"),
            Param("mode", Arg.INTEGER, "Sum of 1/2/4/8/16 schedule modes"),
            Param("cst", Arg.POINT, "Virtual LAO storing calculated start time"),
            Param("csp", Arg.POINT, "Virtual LAO storing calculated stop time"),
            Param("est", Arg.TIME, "Earliest start time"),
            Param("lst", Arg.TIME, "Latest start time"),
            Param("ost", Arg.TIME, "Occupancy start time"),
            Param("esp", Arg.TIME, "Earliest stop time"),
            Param("lsp", Arg.TIME, "Latest stop time"),
            Param("osp", Arg.TIME, "Occupancy stop time"),
            Param("ast", Arg.VALUE, "Adjustment to calculated start time"),
            Param("asp", Arg.VALUE, "Adjustment to calculated stop time"),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "SSTO only calculates times; TOD and TODSET still command the "
            "points. An SSTO whose cst/csp are never read by anything does "
            "nothing at all -- see rule W337.",
            "cst and csp must be virtual LAO points. They receive the "
            "calculated start and stop times.",
            "zone is 1 to 5. mode uses the TODMOD scheme -- 1 normal, "
            "2 extended, 4 shortened, 8 weekend, 16 holiday -- and values may "
            "be summed. Mode 16 only with HOLIDA.",
            "est/lst/ost and esp/lsp/osp are earliest, latest and occupancy "
            "times. The earliest and latest values are the clamps: SSTO never "
            "schedules outside them however the arithmetic comes out.",
            "ast and asp are the self-tuning adjustments, carried day to day. "
            "SSTO nudges them by SSTOCO's coef4 when the zone misses its "
            "target, so the optimisation improves over a season. Passing 0 "
            "displays the current adjustment; passing a virtual LAO lets an "
            "operator seed it.",
            "If SSTOCO sets season = 0 the optimisation is disabled and "
            "cst/csp simply receive the latest start and stop times.",
        ),
        see_also=("SSTOCO", "TOD", "TODSET"),
    )
)
_add(
    Command(
        "SSTOCO",
        "Define the thermal coefficients of an SSTO zone",
        fixed=(
            Param("zone", Arg.INTEGER, "SSTO zone number, 1-5"),
            Param("season", Arg.VALUE, "2 = heat, 1 = cool, 0 = disable SSTO"),
            Param("intemp", Arg.POINT, "Indoor zone temperature"),
            Param("outemp", Arg.POINT, "Outdoor air temperature"),
            Param("ctemp", Arg.VALUE, "Desired cooling-season zone temperature"),
            Param("ccoef1", Arg.NUMBER, "Cooling coefficient (hours per degree)"),
            Param("ccoef2", Arg.NUMBER, "Cooling retention coefficient"),
            Param("ccoef3", Arg.NUMBER, "Cooling transfer coefficient"),
            Param("ccoef4", Arg.NUMBER, "Cooling auto-tune coefficient"),
            Param("htemp", Arg.VALUE, "Desired heating-season zone temperature"),
            Param("hcoef1", Arg.NUMBER, "Heating coefficient (hours per degree)"),
            Param("hcoef2", Arg.NUMBER, "Heating retention coefficient"),
            Param("hcoef3", Arg.NUMBER, "Heating transfer coefficient"),
            Param("hcoef4", Arg.NUMBER, "Heating auto-tune coefficient"),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "Every coefficient is in FRACTIONS OF AN HOUR per degree F. 0.1 "
            "means six minutes. Entering minutes is the usual mistake and "
            "makes the optimisation wildly wrong.",
            "coef1 is the pull-up/pull-down rate: hours to move the zone one "
            "degree with the equipment running, ignoring external load.",
            "coef2 is retention (drift): hours to lose one degree with the "
            "equipment OFF and the outside-air dampers OPEN.",
            "coef3 is transfer: hours to move one degree with the dampers "
            "CLOSED, i.e. against envelope loss alone.",
            "coef2 and coef3 are defined against a REFERENCE outdoor delta, "
            "and the two seasons use different ones: 10 degrees above the "
            "desired temperature for cooling, 25 degrees below it for "
            "heating. That is why the formulas divide by 10 and 25 -- they "
            "rescale the coefficient to the actual outdoor delta.",
            "coef4 is the self-tuning step: hours added to or taken off "
            "SSTO's ast/asp each time the zone misses its target.",
            "season is read live, so a point can swing the zone between "
            "heating and cooling coefficients. season = 0 disables SSTO.",
            "intemp is usually an averaged zone temperature rather than one "
            "sensor; a single unrepresentative sensor makes every start time "
            "wrong in the same direction.",
        ),
        see_also=("SSTO", "TOD", "TODMOD"),
    )
)

# -- peak demand limiting --------------------------------------------------

_add(
    Command(
        "PDL",
        "Shed and restore a group of loads to hold a kW target",
        fixed=(Param("area", Arg.INTEGER, "Meter area number"),),
        repeat=(Param("arg", Arg.VALUE, "Group start/end line and shed mode"),),
        min_repeat=2,
        max_repeat=14,
        time_based=True,
        subroutine_safe=False,
        notes=(
            "Physical form: PDL(totkw,target,g1s,g1e,...,g4s,g4e).",
            "Logical form: PDL(area,totkw,target,g1s,g1e,sh1,...,g4s,g4e,sh4).",
            "Group 1 sheds first, group 4 last.",
            "Each group is a RANGE OF LINE NUMBERS -- gNs and gNe delimit the "
            "block of PDLDAT statements that define that group's loads. shN "
            "is the order within the group: 0 fixed, 1 round robin.",
            "SHEDDING BEGINS AT 90% OF THE SETPOINT, not at it.",
            "PDL must control 10-20% of the building demand to be effective. "
            "Below that it cannot move the peak.",
            "totkw must be the SAME virtual LAO point as the owning PDLDPG's "
            "kwtot, and target the same as PDLDPG's target. PDL calculates "
            "totkw; PDLDPG calculates target.",
            "A load is only under PDL control when all of these hold: it is "
            "named by a PDLDAT associated with this PDL, it currently sits at "
            "NONE or PDL priority, and the PDL, PDLDPG and PDLDAT statements "
            "are all enabled. Anything commanded above PDL priority is simply "
            "not available to shed.",
            "The statement fails if more than four groups are defined.",
            "Unused groups: firmware 2.6 and later need no entry; 2.5.2 and "
            "earlier want integer 0 for the start line, end line and shedding "
            "type of every unused group.",
            "Logical and CM firmware allow only one meter per field panel; "
            "APOGEE allows one per program.",
        ),
        see_also=("PDLDAT", "PDLDPG", "PDLMTR", "PDLSET"),
    )
)
_add(
    Command(
        "PDLDAT",
        "Define one load's timing and kW rating for PDL",
        fixed=(
            Param("ptname", Arg.POINT, "The load point"),
            Param("minon", Arg.NUMBER, "Minimum ON minutes after restore, < 546"),
            Param("minoff", Arg.NUMBER, "Minimum OFF minutes before restore, < 546"),
            Param("maxoff", Arg.NUMBER, "Maximum OFF minutes, <= minoff + 546"),
            Param("kwval", Arg.NUMBER, "Kilowatt rating of the load"),
        ),
        notes=(
            "A PDLDAT should be referenced by only one PDL statement; multiple "
            "references produce unpredictable results.",
            "The load must reside in the same field panel as its PDL statement.",
        ),
        see_also=("PDL", "PDLDPG", "PDLMTR", "PDLSET"),
    )
)
_add(
    Command(
        "PDLDPG",
        "Distribute a demand target across PDL groups in a meter area",
        fixed=(Param("area", Arg.INTEGER, "Meter area, must match PDLMTR"),),
        repeat=(
            Param("kwtot", Arg.POINT, "Virtual LAO holding group consumption"),
            Param("target", Arg.POINT, "Virtual LAO holding group target"),
        ),
        min_repeat=1,
        max_repeat=7,
        time_based=True,
        subroutine_safe=False,
        notes=("Each kwtot LAO should have slope 1, intercept 0, COV limit 1.",),
        see_also=("PDL", "PDLDAT", "PDLMTR", "PDLSET"),
    )
)
_add(
    Command(
        "PDLMTR",
        "Define meters and demand prediction for a meter area",
        fixed=(
            Param("area", Arg.INTEGER, "Meter area, 1 to 32767"),
            Param("hist", Arg.NUMBER, "Historical weighting percent, recommend 30"),
            Param("calc", Arg.NUMBER, "Calculation interval in minutes, minimum 1"),
            Param("window", Arg.NUMBER, "Sliding window minutes, max 30 samples"),
            Param("plot", Arg.NUMBER, "Full-scale kW for the demand plot"),
            Param(
                "warning",
                Arg.ENUM,
                "1 = warnings enabled, 0 = disabled",
                choices=("0", "1"),
            ),
        ),
        repeat=(
            Param("mt", Arg.POINT, "Demand/consumption meter point"),
            Param(
                "def",
                Arg.NUMBER,
                "Default kW if the meter cannot be read, or -1 for last good reading",
            ),
        ),
        min_repeat=1,
        max_repeat=5,
        time_based=True,
        subroutine_safe=False,
        notes=(
            "Analog meter points are in kW; LPACI meter points are in kWH.",
            "hist should be below 50% to anticipate demand.",
            "Only one PDLMTR per meter area (per program on APOGEE).",
        ),
        see_also=("PDL", "PDLDAT", "PDLDPG", "PDLSET"),
    )
)
_add(
    Command(
        "PDLSET",
        "Define time-of-day demand set points for a meter area",
        fixed=(
            Param("area", Arg.INTEGER, "Meter area number"),
            Param("exceed", Arg.POINT, "DO point pulsed when the set point was exceeded"),
        ),
        repeat=(
            Param("set", Arg.NUMBER, "Demand set point in kW"),
            Param("time", Arg.TIME, "Time this set point ends"),
        ),
        min_repeat=2,
        max_repeat=7,
        time_based=True,
        subroutine_safe=False,
        notes=(
            "At least two set point/time pairs are required per day or no "
            "reports will generate.",
            "Times must be in ascending order.",
        ),
        see_also=("PDL", "PDLDAT", "PDLDPG", "PDLMTR"),
    )
)

# -- declarations and macros ----------------------------------------------

_add(
    Command(
        "LOCAL",
        "Declare up to 16 program-local virtual points",
        **_points(1, 16),
        notes=(
            "Referenced inside the program as $name.",
            "Other programs reference them as PROGRAM:name.",
        ),
    )
)
_add(
    Command(
        "DEFINE",
        "Create a text abbreviation for a long point-name prefix",
        fixed=(
            Param("abbrev", Arg.VALUE, "Abbreviation, used as %abbrev% elsewhere"),
            Param("string", Arg.STRING, "Text substituted for the abbreviation"),
        ),
        firmware=frozenset({Firmware.APOGEE}),
        notes=(
            "Executed when added to the panel; it does not need enabling and "
            "does not participate in normal program flow.",
            "Reference the abbreviation with percent signs on both sides.",
        ),
    )
)
_add(
    Command(
        "OIP",
        "Execute an operator keystroke sequence from within PPCL",
        fixed=(
            Param("trigger", Arg.POINT, "LDO/LDI point or local that fires the sequence"),
            Param("seq", Arg.STRING,
                  "Keystroke sequence, max 80 characters including the "
                  "slashes, quoted, one / per menu level"),
        ),
        firmware=frozenset(
            {Firmware.PHYSICAL, Firmware.LOGICAL, Firmware.UNITARY,
             Firmware.CM, Firmware.APOGEE}
        ),
        notes=(
            "REMOVED ON PXC.A. A6V10374898 states it outright: \"This "
            "statement is no longer supported in PXC.A devices. The PXC.A "
            "PPCL runtime will consider this statement invalid, and no "
            "replacements have been provided.\" A program carrying OIP will "
            "not run on a PXC4/5/7.A -- see rule E119.",
            "Used for generating reports, changing point priorities, sending "
            "messages and triggering auto-dial.",
            "The trigger must be toggled OFF then ON to run the sequence "
            "again. After a power failure, an ENABLE, or the first pass after "
            "a database load, it will not fire until the trigger toggles.",
            "Stagger OIP commands in time; do not share one trigger point, or "
            "one sequence starts before the previous finishes.",
            "Shows as FAILED if the keystroke sequence is wrong.",
            "Must be evaluated every pass to see the trigger change.",
            "Cannot be used for loop tuning.",
            "A slash is a carriage return inside the sequence.",
            "With an LDO subpoint, command the trigger with 1 or 0 -- any "
            "other text makes the statement fail.",
            "If the TRIGGER point name starts with a digit it needs a leading "
            "@, but a point named inside the keystroke sequence must NOT have "
            "one: OIP(TRIG,\"P/T/D/H///FAN/1FAN//60/\") is right, and the "
            "same with @1FAN is wrong.",
            "A pause in a phone number is a comma on APOGEE, a period on "
            "pre-APOGEE.",
        ),
    )
)

# -- adaptive control and least-squares curve fit ---------------------------
#
# Signatures transcribed from the Desigo CC PPCL Editor's Command Assist,
# which renders the parameter list and description for each command. These are
# not in 125-1896 Rev. 5.

_add(
    Command(
        "ADAPTM",
        "Adaptive control of one temperature through a sequenced "
        "heating/damper/cooling output",
        fixed=(
            Param("pv", Arg.POINT,
                  "Process variable, normally supply air temperature. "
                  "-50.0 to 150.0"),
            Param("cv", Arg.POINT,
                  "Output, 0.0-100.0 percent DIRECT acting. Feed it to one "
                  "TABLE per actuator"),
            Param("sp", Arg.VALUE, "Setpoint, -50.0 to 150.0, same units as pv"),
            Param("matctl", Arg.VALUE,
                  "Output of the mixed-air control loop, usually an ADAPTS. "
                  "100.0 when there is no damper override"),
            Param("mam", Arg.VALUE,
                  "Mixed-air damper minimum position, 0.0-100.0. 0.0 when "
                  "there are no dampers"),
            Param("st", Arg.VALUE,
                  "Sample time in seconds; at least 1 and no more than a "
                  "third of the smallest time constant"),
            Param("kc", Arg.VALUE,
                  "Control gain, greater than 0. 3.0 English, 6.0 SI"),
            Param("tcd", Arg.VALUE,
                  "Damper time constant in seconds, at least 3 x st"),
            Param("tch", Arg.VALUE,
                  "Heating coil time constant in seconds, at least 3 x st"),
            Param("tcc", Arg.VALUE,
                  "Cooling coil time constant in seconds, at least 3 x st"),
            Param("her", Arg.VALUE,
                  "Heating end of range, percent. Heating occupies 0 to her"),
            Param("dbr", Arg.VALUE,
                  "Damper beginning of range, percent. At least her"),
            Param("der", Arg.VALUE,
                  "Damper end of range, percent. Cooling occupies der to 100"),
            Param("err", Arg.POINT,
                  "Error reporting point. Zero means no error. Must be unique "
                  "to this statement"),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "One adaptive loop driving a SEQUENCED output. The single 0-100% "
            "cv is split into ranges: 0 to her is heating, her to dbr is the "
            "deadband, dbr to der is the mixed-air dampers (free cooling), "
            "and der to 100 is mechanical cooling. The ordering constraint is "
            "0 <= her <= dbr <= der <= 100.",
            "No heating: set her to 0. No dampers: set dbr equal to der, "
            "matctl to 100.0 and mam to 0.0.",
            "cv is direct acting and is meant to drive a TABLE per actuator, "
            "which is where each device's own stroke range is applied.",
            "Low-limit interaction: when matctl falls to or below mam, cv is "
            "held at or below her -- that is, the loop is not allowed past "
            "heating while the mixed-air loop is fighting a low limit.",
            "Adaptive control requires the process to be open-loop stable, "
            "consistently direct or reverse acting across the whole control "
            "range, MODULATING, and free of excessive dead time (sensor plus "
            "actuator delay no more than twice the process time constant).",
            "DX cooling and step-controlled electric heat CANNOT be driven by "
            "ADAPTM or ADAPTS -- they are not modulating.",
            "Every ADAPTM statement needs its OWN error point. Sharing one "
            "between statements is called out as a caution in the manual.",
            "Increasing a time constant slows adaptation.",
            "The Soft Controller does not support adaptive control.",
            "Compatible only with firmware version 2.7 or above.",
            "Parameter list from A6V10374898 Ch.3; the Desigo CC Command "
            "Assist shows only pt1..pt14 for this command.",
        ),
        see_also=("ADAPTS", "LOOP", "TABLE"),
    )
)
_add(
    Command(
        "ADAPTS",
        "Adaptive control of one process variable through one output",
        fixed=(
            Param("pv", Arg.POINT,
                  "Process variable being controlled, between llpv and hlpv"),
            Param("cv", Arg.POINT,
                  "Output, 0.0-100.0 percent DIRECT acting. Feed it to a "
                  "TABLE per actuator"),
            Param("sp", Arg.VALUE,
                  "Setpoint, between llpv and hlpv, same units as pv"),
            Param("st", Arg.VALUE,
                  "Sample time in seconds; at least 1 and no more than tc/3"),
            Param("kc", Arg.VALUE,
                  "Control gain, greater than 0. Set 3.0 for both English and "
                  "SI -- the loop adapts from there"),
            Param("tc", Arg.VALUE,
                  "Process time constant in seconds, at least 3 x st. Larger "
                  "slows adaptation"),
            Param("ra", Arg.VALUE, "1 = reverse acting, 0 = direct acting"),
            Param("llpv", Arg.VALUE, "Low limit of the process variable"),
            Param("hlpv", Arg.VALUE, "High limit of the process variable"),
            Param("llcv", Arg.VALUE,
                  "Low limit of the output. 0.0 for an electric actuator, the "
                  "bottom of the spring range for a pneumatic one"),
            Param("hlcv", Arg.VALUE,
                  "High limit of the output. 100.0 for an electric actuator, "
                  "the top of the spring range for a pneumatic one"),
            Param("edb", Arg.VALUE,
                  "Error deadband; at least 0.0. The effective setpoint "
                  "becomes sp +/- edb"),
            Param("npv", Arg.VALUE,
                  "1 when the process variable is noisy (slows adaptation), "
                  "0 when it is not"),
            Param("err", Arg.POINT,
                  "Error reporting point. Zero means no error. Must be unique "
                  "to this statement"),
        ),
        time_based=True,
        subroutine_safe=False,
        notes=(
            "One adaptive loop, one output. ADAPTM is the version whose "
            "single output is split across heating, dampers and cooling.",
            "kc is 3.0 for English AND SI here, unlike ADAPTM where SI is "
            "6.0, because ADAPTS adapts to the process from that start point.",
            "Suggested sample times: 5 s fast / 10 s slow temperature loops; "
            "5-10 s return or space humidity, 1-2 s discharge humidity; "
            "1-2 s flow and static pressure.",
            "Suggested time constants: mixed air about 40 s; duct static and "
            "airflow 6 / 10 / 20 s for small / medium / large; duct humidity "
            "50 / 100 / 200 s; the outer loop of a cascade 100 / 250 / 500 s. "
            "A coil's time constant is computed from design airflow, design "
            "water flow, sensor time constant and actuator stroke time.",
            "edb exists to stop a noisy signal driving the actuator back and "
            "forth. Set it to the loop's allowable setpoint tolerance: 0.0 is "
            "acceptable for temperature and humidity, 1-3% of full scale is a "
            "starting point for flow and static pressure. The cost is accuracy "
            "near setpoint.",
            "npv = 1 for airflow or static pressure, 0 for temperature or "
            "humidity.",
            "Adaptive control requires the process to be open-loop stable, "
            "consistently direct or reverse acting across the whole control "
            "range, MODULATING, and free of excessive dead time (sensor plus "
            "actuator delay no more than twice the process time constant).",
            "DX cooling and step-controlled electric heat CANNOT be driven by "
            "ADAPTS or ADAPTM -- they are not modulating.",
            "Every ADAPTS statement needs its OWN error point.",
            "The Soft Controller does not support adaptive control.",
            "Compatible only with firmware version 2.7 or above.",
            "Parameter list from A6V10374898 Ch.3; the Desigo CC Command "
            "Assist shows only pt1..pt14 for this command.",
        ),
        see_also=("ADAPTM", "LOOP", "TABLE"),
    )
)
_add(
    Command(
        "LSQ2",
        "Two-variable quadratic least-squares curve fit",
        fixed=(Param("execution", Arg.VALUE, "Execution time in minutes"),),
        repeat=(Param("pt", Arg.POINT, "curve fit coefficient point"),),
        min_repeat=1,
        max_repeat=6,
        trailing=(
            Param("startline", Arg.LINE, "First LSQDAT data line"),
            Param("endline", Arg.LINE, "Last LSQDAT data line"),
        ),
        notes=(
            "The XYZ Least Squares Curve Fit, an enhancement to the Cooling "
            "Plant Optimization Package (CPOP) for modelling a chiller's "
            "part-load performance.",
            "EXACTLY 8 LINES: this statement, then SEVEN LSQDAT lines. The "
            "trailing startline# and endline# delimit that block.",
            "The trailing startline# and endline# are PPCL LINE NUMBERS. "
            "Renumbering the program must rewrite them, and does.",
            "The six coefficient points receive the result and should be "
            "virtual LAO points, so the model updates as the plant changes.",
            "Coefficients generally fall between 0.0 and 1.0, so define those "
            "points with a slope of 0.001 to get usable resolution.",
            "Signature from the PPCL Editor Command Assist; the execution "
            "parameter, the 8-line structure and the CPOP context are from "
            "the Insight Program Editor help.",
        ),
        see_also=("LSQDAT",),
    )
)
_add(
    Command(
        "LSQDAT",
        "Supply one row of input values to an LSQ2 curve fit",
        fixed=(
            Param("pt1", Arg.POINT, "x input for this row"),
            Param("pt2", Arg.POINT, "y input for this row"),
            Param("pt3", Arg.POINT, "z input for this row"),
        ),
        notes=(
            "Exactly SEVEN LSQDAT lines follow each LSQ2, one per row of the "
            "curve fit.",
            "The LSQ2 program line fails if any of the data lines are "
            "unresolved or failed, or if a calculation produces a result too "
            "small to be recognised as a usable value.",
            "Must lie within the startline#/endline# range named by its LSQ2.",
            "The three arguments may be floating-point literals instead of "
            "points. That gives a STATIC curve -- the model no longer tracks "
            "the plant, which is usually not what was wanted.",
            "Signature from the PPCL Editor Command Assist; the seven-row "
            "structure and the static-curve note are from the Insight Program "
            "Editor help.",
        ),
        see_also=("LSQ2",),
    )
)

# --------------------------------------------------------------------------
# Statements removed from the language on PXC.A
# --------------------------------------------------------------------------

#: Transcribed from A6V10374898, "Obsolete PPCL Statements Removed from the
#: Language": "The following statements are not supported in PXC.A devices.
#: The PXC.A PPCL runtime will consider these statements to be invalid, and no
#: replacements are provided."
#:
#: Applied below rather than written into each Command, so the list reads as
#: one table against one source -- which is how the manual presents it, and
#: how anyone checking it will want to compare.
PXC_A_REMOVED = {
    "ADAPTM": "The ADAPT application is not supported in PXC.A devices.",
    "ADAPTS": "The ADAPT application is not supported in PXC.A devices.",
    "DISCOV": "Programmatic COV enable/disable is not supported in PXC.A "
              "devices.",
    "ENCOV": "Programmatic COV enable/disable is not supported in PXC.A "
             "devices.",
    "DPHONE": "Dialup modems are not supported in PXC.A devices.",
    "EPHONE": "Dialup modems are not supported in PXC.A devices.",
    "ALARM": "Commanding into and out of alarm state is not supported in "
             "PXC.A devices.",
    "NORMAL": "Commanding into and out of alarm state is not supported in "
              "PXC.A devices.",
    "OIP": "The PRMMI, along with its menu prompt tree, is not supported in "
           "PXC.A devices. Siemens says a limited alternative may be provided "
           "in a future release.",
    "ONPWRT": "PXC.A devices do not have warmstart functionality, therefore a "
              "PPCL program will ALWAYS start at the first line after a power "
              "failure.",
}


def _apply_pxc_a_removals() -> None:
    """Drop PXC_A from every command the PXC.A runtime rejects.

    ``ONPWRT`` is the one with a consequence beyond the statement itself: with
    no warmstart, a PXC.A program always resumes at line 1 after a power
    failure, so there is nothing for ONPWRT to do and no way to do it.
    """
    import dataclasses

    for name, reason in PXC_A_REMOVED.items():
        cmd = ALL.get(name)
        if cmd is None:
            raise AssertionError("PXC_A_REMOVED names %s, which is not a "
                                 "command" % name)
        families = frozenset(f for f in cmd.firmware if f is not Firmware.PXC_A)
        notes = cmd.notes
        if not any("PXC.A" in n for n in notes):
            notes = notes + ("REMOVED ON PXC.A. " + reason,)
        ALL[name] = dataclasses.replace(cmd, firmware=families, notes=notes)


_apply_pxc_a_removals()


_add(
    Command(
        "LSTSQR",
        "Recursive least-squares fit of a quadratic through six (x,y) pairs",
        fixed=(
            Param("execution", Arg.VALUE, "Execution control, as for LSQ2"),
            Param("c", Arg.POINT, "Constant term of the fitted quadratic"),
            Param("b", Arg.POINT, "Linear coefficient"),
            Param("a", Arg.POINT, "Quadratic coefficient"),
        ),
        repeat=(
            Param("x", Arg.POINT, "x of a data pair"),
            Param("y", Arg.POINT, "y of a data pair"),
        ),
        min_repeat=1,
        max_repeat=6,
        # NOT transcribed. This command appears in NO Siemens documentation on
        # hand -- not the 736-page Insight Program Editor help, not the Desigo
        # CC Engineering or Operating help, not A6V10374898, A6V12954388 or
        # A6V10324350. It was recovered from Siemens' own shipped application
        # library, where the chiller-sequence programs chseq2 through chseq5
        # use it and nothing else does.
        #
        # The shape is inferred from nine call sites, which all pass an
        # execution control, three output points, and six (x,y) pairs. The
        # ORDER of the three outputs comes from what those programs compute
        # from them a few lines later: an expression of the form
        # -second_output / 2 / third_output, which is the vertex of a
        # parabola, -b/(2a). That fixes the order as constant, linear,
        # quadratic -- c, b, a.
        #
        # So: the command is real, the ORDER is evidence-based, and the
        # argument COUNT is not enforced, because no source states it.
        signature_known=False,
        notes=(
            "UNDOCUMENTED. Recovered from Siemens' shipped PPCL application "
            "library (chseq2-chseq5); it appears in no manual available here.",
            "Comments in those programs call it a 'recursive least squares "
            "curve fit' producing 'the coefficients of the best fit quadratic "
            "curve'.",
            "Argument order c, b, a is inferred from how the programs use the "
            "results: they compute -b/(2a), the vertex of a parabola.",
            "Every observed call passes exactly six (x,y) pairs, which with "
            "the four leading arguments is 16 operands -- the statement limit.",
            "Distinct from LSQ2/LSQDAT, which fit a two-variable XYZ surface "
            "across eight lines. The library uses LSTSQR and never LSQ2.",
        ),
        see_also=("LSQ2", "LSQDAT", "TABLE"),
    )
)

#: PARAMETER is handled separately: it is an assignment-style directive that
#: does not require a line number and is resolved at compile time.
PARAMETER_KEYWORD = "PARAMETER"


# --------------------------------------------------------------------------
# Reserved words
# --------------------------------------------------------------------------


def _build_reserved() -> frozenset:
    words = set(ALL)
    words |= {"IF", "THEN", "ELSE", "C", PARAMETER_KEYWORD}
    words |= set(FUNCTIONS)
    words |= STATUS_INDICATORS
    words |= set(RESIDENT_POINTS)
    words |= {p.lstrip("@") for p in PRIORITY_ORDER}
    words |= set(PRIORITY_ORDER)
    words |= {op.strip(".") for op in DOTTED_OPS}
    words |= DOTTED_OPS
    # Word forms that are reserved names without a documented operator
    # syntax: no manual shows a statement using EQUAL or LESS, and outside
    # the reserved-word lists they appear only inside comment prose in
    # worked examples ("C IF THE ROOM TEMP IS LESS THAN 80,").
    #
    # Both have an obvious failure mode that has to be ruled out, because
    # another project reading the same language fell into it: Desigo CC's
    # glossary titles topics "Equal To - EQ" and "Less Than - LT", so a
    # description column read as tokens manufactures EQUAL and LESS out of
    # nothing. Ruled out here. The Program Editor's shipped reserved-word
    # page is a two-column table of bare tokens with no description column
    # anywhere in it -- the strings "Equal To" and "Less Than" do not occur
    # on the page -- and EQUAL and LESS each occupy their own alphabetical
    # cell, EQUAL between EQ and EXP, LESS between LE and LINK.
    #
    # EQUAL is then confirmed twice: 125-1896 Rev. 5 Chapter 5's list has
    # it in the same position, also bare. LESS is in the Program Editor
    # list only; Chapter 5 goes straight from LE to LINK, and Desigo CC
    # ships no enumerated list at all. One Siemens source reserves it and
    # none contradicts, so it is reserved -- over-reserving costs a W107 on
    # a point name nobody chose, under-reserving misses one a panel refuses.
    words |= {"EQUAL", "LESS", "NOR", "AND", "OR", "NOT"}
    for i in range(1, LOCAL_ARG_COUNT + 1):
        words |= {"$ARG%d" % i, "ARG%d" % i}
    for i in range(1, LOCAL_LOC_COUNT + 1):
        words |= {"$LOC%d" % i, "LOC%d" % i}
    for prefix, lo, hi in RESIDENT_RANGES:
        for i in range(lo, hi + 1):
            words.add("%s%d" % (prefix, i))
    return frozenset(words)


#: The PPCL Reserved Word List (manual, Chapter 5) plus every command name.
RESERVED_WORDS = _build_reserved()


def is_resident(name: str) -> bool:
    """True if ``name`` is a system-maintained point that always exists."""
    upper = name.upper()
    if upper in RESIDENT_POINTS:
        return True
    for prefix, lo, hi in RESIDENT_RANGES:
        if upper.startswith(prefix):
            suffix = upper[len(prefix) :]
            if suffix.isdigit() and lo <= int(suffix) <= hi:
                return True
    return False


def is_builtin_local(name: str) -> bool:
    """True for the always-available $ARGn and $LOCn local variables."""
    upper = name.upper().lstrip("$")
    for prefix, count in (("ARG", LOCAL_ARG_COUNT), ("LOC", LOCAL_LOC_COUNT)):
        if upper.startswith(prefix):
            suffix = upper[len(prefix) :]
            if suffix.isdigit() and 1 <= int(suffix) <= count:
                return True
    return False


#: Commands whose evaluation depends on wall-clock time and which therefore
#: must be reached on every program pass. Manual, Chapter 2 "PPCL guidelines".
TIME_BASED_COMMANDS = frozenset(name for name, cmd in ALL.items() if cmd.time_based)

#: Commands the manual forbids inside a GOSUB subroutine.
SUBROUTINE_UNSAFE = frozenset(
    name for name, cmd in ALL.items() if not cmd.subroutine_safe
)

#: Commands the manual forbids as the THEN or ELSE clause of an IF.
IF_TARGET_UNSAFE = frozenset(
    name for name, cmd in ALL.items() if not cmd.if_target_safe
)


# --------------------------------------------------------------------------
# Command Assist categories
# --------------------------------------------------------------------------

#: The command list filter categories used by the Desigo CC PPCL Editor's own
#: Command Assist, transcribed verbatim from the engineering help ("PPCL Editor
#: Workspace" > "Command Assist" > "Available Commands"). The workbench uses
#: the same names and the same grouping so that an engineer who already knows
#: the Desigo editor finds commands in the place they expect.
#:
#: Two entries in that list are not commands in this spec and are excluded
#: here: ``IF`` is a statement rather than a command, and ``ROOT`` is the
#: ``.ROOT.`` operator. Both are still offered by the editor's completion.
#:
#: Note also that the same help page lists ``ATN`` in this command list while
#: its precedence table spells the same function ``ARC``. That is a second,
#: independent confirmation from Siemens' own document that ATN is correct.
COMMAND_CATEGORIES = {
    "Program Control": [
        "ACT", "DEACT", "ENABLE", "DISABL", "EPHONE", "DPHONE", "GOSUB",
        "GOTO", "ONPWRT", "SAMPLE", "RETURN",
    ],
    "Point Control": [
        "ON", "OFF", "FAST", "SLOW", "AUTO", "SET", "INITTO", "WAIT", "STATE",
    ],
    "Operational Control": [
        "ENALM", "DISALM", "ALARM", "NORMAL", "LLIMIT", "HLIMIT",
        "ENCOV", "DISCOV",
    ],
    "Emergency Control": [
        "EMON", "EMOFF", "EMFAST", "EMSLOW", "EMAUTO", "EMSET", "RELEAS",
    ],
    "Energy Management": [
        "DAY", "NIGHT", "DC", "DCR", "TOD", "TODMOD", "TODSET", "HOLIDA",
        "SSTO", "SSTOCO", "PDL", "PDLDAT", "PDLMTR", "PDLSET", "PDLDPG",
        "LOOP", "ADAPTM", "ADAPTS",
    ],
    "Special Function": [
        "MIN", "MAX", "DBSWIT", "TABLE", "TIMAVG", "OIP", "DEFINE", "LOCAL",
        "LSQ2", "LSQDAT",
    ],
    "Arithmetic Function": [
        "ATN", "COM", "COS", "EXP", "LOG", "SIN", "SQRT", "TAN",
        "ALMPRI", "TOTAL",
    ],
    # NOT one of Siemens' Command Assist categories. GETVAL and SETVAL are
    # documented in A6V10374898 (the PXC.A guide) but do not appear in the
    # Desigo CC Command Assist command list at all, so there is no Siemens
    # category to put them in. Kept separate rather than filed under an
    # existing heading, so the categories above stay a verbatim transcription.
    "Property Access": [
        "GETVAL", "SETVAL",
    ],
    # Also NOT a Siemens category, and for a stronger reason: LSTSQR appears
    # in no Siemens documentation at all. It was recovered from their own
    # shipped application library. Filing it under an existing heading would
    # imply a source that does not exist.
    "Undocumented": [
        "LSTSQR",
    ],
}

#: The subset of COMMAND_CATEGORIES that is a verbatim transcription of the
#: Desigo CC Command Assist filter list. Anything outside it is this project's
#: own grouping and is labelled as such in the UI.
SIEMENS_COMMAND_CATEGORIES = frozenset(
    COMMAND_CATEGORIES
) - {"Property Access", "Undocumented"}


def category_of(name: str):
    """The Command Assist category a command belongs to, or None."""
    upper = name.upper()
    for category, names in COMMAND_CATEGORIES.items():
        if upper in names:
            return category
    return None


def _check_categories():
    """Every command in the spec must appear in exactly one category."""
    seen = {}
    for category, names in COMMAND_CATEGORIES.items():
        for n in names:
            if n in seen:
                raise AssertionError(
                    "%s is listed in both %s and %s" % (n, seen[n], category)
                )
            seen[n] = category
    uncategorised = sorted(set(ALL) - set(seen))
    if uncategorised:
        raise AssertionError(
            "commands missing from COMMAND_CATEGORIES: %s"
            % ", ".join(uncategorised)
        )


_check_categories()


# --------------------------------------------------------------------------
# Panel error codes
# --------------------------------------------------------------------------

#: Errors the *field panel* reports, as opposed to findings this toolkit
#: derives. Transcribed from the APOGEE BACnet ALN Field Panel User's Manual
#: (A6V10324350 / 125-3020), Appendix C.
#:
#: Two sets, and the distinction matters. **R-codes come from the PPCL
#: compiler** -- the line was refused when it was entered or downloaded.
#: **E-codes come from the running system** -- the line is loaded and the panel
#: failed while carrying it out. A rule that predicts an R-code is saying "this
#: will not load"; one that predicts an E-code is saying "this will load and
#: then not work", which is the more dangerous of the two.
#:
#: Only the codes that bear on writing PPCL are transcribed. The manual's full
#: E-code list runs into the thousands and covers cassette tapes, report
#: printers and FLN drops.

#: Peak Demand Limiting is five commands, and the manual states an order they
#: must be defined in: "Distributed PDL uses five PPCL commands that must be
#: defined in the following order" -- Insight Program Editor, Peak Demand
#: Limiting. Not every program has all five; see PDL_ROLES.
PDL_COMMAND_ORDER = ("PDLMTR", "PDLSET", "PDLDPG", "PDL", "PDLDAT")

#: Which panel carries which of them. "The predictor field panel must have the
#: PDLMTR, PDLSET, and PDLDPG commands defined in its PPCL program. Each
#: load-handler field panel must have the PDL and PDLDAT statements defined in
#: its PPCL program." A predictor that also controls loads carries all five.
PDL_ROLES = {
    "predictor": ("PDLMTR", "PDLSET", "PDLDPG"),
    "load_handler": ("PDL", "PDLDAT"),
}


#: ``{code: (text, explanation)}`` -- the PPCL compiler's own errors.
PPCL_COMPILER_ERRORS = {
    "R0": ("Line not accepted, cause unknown",
           "The panel refused the line but cannot say why. Examine it, "
           "recompose it and re-enter."),
    "R1": ("Invalid line number",
           "A line was entered without a valid number. Must be an integer "
           "from 1 to 32,767."),
    "R2": ("Unrecognized statement",
           "A typographical mistake is the usual cause -- ONN for ON, LOP for "
           "LOOP."),
    "R3": ("Invalid RETURN statement",
           "RETURN has been used incorrectly in the line."),
    "R5": ("Invalid control statement",
           "Most often an attempt to command an analog point with a digital "
           "statement or the reverse, for example OFF(DAMPER) where DAMPER is "
           "analog."),
    "R6": ("Invalid IF statement",
           "The IF is used incorrectly or improperly constructed."),
    "R7": ("Invalid ASSIGNMENT statement",
           "An illegal value for the point, for example assigning a decimal "
           "to a digital point."),
    "R8": ("Unbalanced parentheses",
           "The number of opening and closing parentheses differs."),
    "R9": ("Line numbers out of order",
           "Program line numbers are not in ascending order."),
    "R10": ("Too many arguments in the statement", ""),
    "R11": ("Too many operands in the statement", ""),
    "R13": ("Invalid binary operator",
            "The binary operator is not recognized. Usually a mistyped "
            "relational operator."),
}

#: ``{code: (hex, text, explanation)}`` -- runtime errors worth knowing when
#: reading PPCL. These fire on a line that compiled cleanly.
PANEL_RUNTIME_ERRORS = {
    "E2": ("0x0002", "Invalid command",
           "A command inappropriate for the point type -- commanding an "
           "analog point ON, or putting a non-alarmable point into "
           "alarm-by-command."),
    "E3": ("0x0003", "Not found",
           "The point is not defined in any online field panel, or the "
           "program line does not exist, or the field panel is not a member "
           "of that network."),
    "E4": ("0x0004", "Priority too low",
           "The point cannot be commanded because the priority of the "
           "statement is lower than the point's current priority. This is the "
           "error behind the single most common PPCL defect: a point "
           "commanded above NONE and never released."),
    "E5": ("0x0005", "No change",
           "The point's condition was already what the program asked for."),
    "E7": ("0x0007", "Failed",
           "The point is failed, which usually means hardware: the panel, the "
           "board, or the termination."),
    "E8": ("0x0008", "Out of service",
           "The point is operator disabled and cannot be altered until it is "
           "re-enabled."),
    "E11": ("0x000B", "Value unchanged",
            "The point is already at the commanded value or state."),
    "E12": ("0x000C", "Value out of range",
            "An analog point was commanded to a value that, given the point's "
            "slope and intercept, puts the digital value outside 0 to 32,767. "
            "Commonly hit by commanding a virtual LAO defined with an "
            "intercept of zero to a NEGATIVE value."),
    "E22": ("0x0016", "Line not traced",
            "The line was accessed but not executed."),
    "E23": ("0x0017", "Line not enabled",
            "The line is disabled and must be enabled before the system can "
            "reach it."),
    "E25": ("0x0019", "Line already exists",
            "A line was added using a number already present in the panel."),
    "E26": ("0x001A", "Has unresolved points",
            "The line names points that cannot be found in any active field "
            "panel on the network."),
    "E28": ("0x001C", "Bad statement type",
            "Loop tuning was attempted on a statement that is not a LOOP."),
    "E31": ("0x001F", "Not set up for TOD",
            "The point is not configured for Time-Of-Day functions."),
    "E34": ("0x0022", "Cannot override",
            "An attempt to override an override statement."),
    "E3605": ("0x0e15", "Physical point not commandable",
              "An attempt to change a physical point that cannot process "
              "commands. Most often an FLN device point."),
    "E3606": ("0x0e16", "Value out of range",
              "A point was commanded outside its physical range."),
}


def panel_error(code: str):
    """Look up a panel error by code. Returns ``(kind, text, explanation)``."""
    key = code.upper().strip()
    if key in PPCL_COMPILER_ERRORS:
        text, why = PPCL_COMPILER_ERRORS[key]
        return ("compiler", text, why)
    if key in PANEL_RUNTIME_ERRORS:
        _hex, text, why = PANEL_RUNTIME_ERRORS[key]
        return ("runtime", text, why)
    return None
