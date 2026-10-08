"""Rule registry and lint driver."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import spec
from .analyzer import Analysis, analyze
from .diagnostics import Diagnostic, Severity
from .parser import Program


@dataclass
class LintContext:
    """Everything a rule is given when it runs."""

    program: Program
    analysis: Analysis
    firmware: spec.Firmware = spec.Firmware.APOGEE
    #: Optional point database: upper-case name -> point type string.
    point_types: dict = field(default_factory=dict)
    #: Rule codes the caller asked to suppress.
    disabled: frozenset = frozenset()
    #: Site settings a rule may consult, from ``ppcl.settings``. Only for a
    #: finding whose RELEVANCE is a property of the site rather than of the
    #: program -- not a second way to switch rules off, which ``disabled``
    #: already does.
    options: dict = field(default_factory=dict)
    #: Memoised :meth:`library_placeholders`; None until first asked.
    _placeholders: object = field(default=None, repr=False, compare=False)

    def option(self, key, default=None):
        return self.options.get(key, default)

    def point_type(self, name: str):
        return self.point_types.get(name.upper().lstrip("$"))

    def library_placeholders(self) -> frozenset:
        """Name-exchange placeholders this program still carries, upper-cased.

        Siemens' application library ships its programs as *templates*. A
        block of comments near the top declares each editable name between
        backslashes -- description, then the token, then a close -- and the
        engineer substitutes them through the editor before loading. An
        unsubstituted token looks exactly like an ordinary point name, so
        without this every rule that checks names reports on text that was
        never meant to survive to a panel.

        Computed once per lint, because three rules want it.
        """
        if self._placeholders is None:
            self._placeholders = _scan_placeholders(self.program)
        return self._placeholders


#: One backslash. Spelled this way because the thing being matched is a
#: backslash-delimited format and a regex for it is otherwise unreadable.
_BS = chr(92)

#: A library name-exchange prompt: a comment whose last backslash-delimited
#: field is a bare token. The prompt block's own header lines carry only an
#: opening and closing backslash and no token, which is what separates a
#: declaration from the prose around it.
_PROMPT = re.compile(
    _BS * 2 + r"([A-Za-z$][A-Za-z0-9$.]{0,13})" + _BS * 2 + r"\s*$"
)


#: A leading DEFINE macro reference, as in "%PFX%DBT". The library's templates
#: declare the *suffix* as the editable name and write the prefix separately,
#: because the prefix is usually the site's own building or panel string: a
#: DEFINE binds it once, and every reference is then written behind it.
_MACRO_PREFIX = re.compile(r"^%[A-Za-z0-9_.]+%")


def placeholder_key(name: str) -> str:
    """Normalise a reference for comparison against a declared placeholder."""
    return _MACRO_PREFIX.sub("", name.upper())


def _scan_placeholders(program) -> frozenset:
    found = set()
    for ln in program.lines:
        if not ln.is_comment:
            continue
        body = ln.body or ""
        if body.count(_BS) < 3:
            continue
        match = _PROMPT.search(body)
        if match:
            found.add(match.group(1).upper())
    return frozenset(found)


#: Registry of rule functions, populated by the @rule decorator.
REGISTRY = []


@dataclass
class Rule:
    """A registered lint rule."""

    code: str
    name: str
    summary: str
    func: object
    default_severity: Severity


def rule(code: str, summary: str, severity: Severity = Severity.WARNING):
    """Register a lint rule.

    The decorated function takes a :class:`LintContext` and yields
    :class:`~ppcl.diagnostics.Diagnostic` objects.
    """

    def wrap(func):
        REGISTRY.append(
            Rule(
                code=code,
                name=func.__name__,
                summary=summary,
                func=func,
                default_severity=severity,
            )
        )
        return func

    return wrap


def _load_rules() -> None:
    """Import the rule modules so their decorators run."""
    from .rules import flow, performance, semantics, style, syntax  # noqa: F401


def lint(
    program: Program,
    firmware: spec.Firmware = spec.Firmware.APOGEE,
    point_types: dict = None,
    disabled=(),
    analysis: Analysis = None,
    options: dict = None,
) -> list:
    """Run every registered rule against ``program``.

    Returns diagnostics sorted by severity then line number.
    """
    _load_rules()
    ctx = LintContext(
        program=program,
        analysis=analysis or analyze(program),
        firmware=firmware,
        point_types={k.upper(): v for k, v in (point_types or {}).items()},
        disabled=frozenset(disabled),
        options=dict(options or {}),
    )

    out = []

    # Parse failures are reported first and are never suppressible.
    for src_no, line_no, message in program.errors:
        out.append(
            Diagnostic(
                code="E100",
                severity=Severity.ERROR,
                message="cannot parse this line: %s" % message,
                line=line_no,
                source_line=src_no,
                program=program.name,
            )
        )

    for r in REGISTRY:
        if r.code in ctx.disabled:
            continue
        for diag in r.func(ctx) or ():
            if diag.code in ctx.disabled:
                continue
            diag.program = diag.program or program.name
            if diag.source_line is None and diag.line is not None:
                ln = ctx.analysis.line_at(diag.line)
                if ln is not None:
                    diag.source_line = ln.source_line
            out.append(diag)

    out.sort(key=lambda d: d.sort_key())
    return out


def rule_catalog() -> list:
    """Every registered rule, for documentation and ``ppcl rules``."""
    _load_rules()
    return sorted(REGISTRY, key=lambda r: r.code)


def summarize(diags: list) -> dict:
    """Count diagnostics by severity."""
    counts = {s.value: 0 for s in Severity}
    for d in diags:
        counts[d.severity.value] += 1
    return counts
