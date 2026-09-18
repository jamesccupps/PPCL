"""Rule registry and lint driver."""

from __future__ import annotations

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

    def point_type(self, name: str):
        return self.point_types.get(name.upper().lstrip("$"))


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
